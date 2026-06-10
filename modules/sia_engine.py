"""
sia_engine.py — Self-Improving Agent (SIA) v1.0 (Hermes v9.2)
==================================================================
사용자 피드백(👍/👎)을 학습하여 액션 성능을 자동 개선.

Priority 1 of v9.2 업그레이드:
  - record_feedback(): 피드백 기록 (SQLite)
  - analyze_trends(): 액션별 평점 분석
  - get_low_performers(): 저성능 액션 감지 (평점 ≤ 3.0)
  - suggest_improvements(): LLM 기반 개선 제안
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

log = logging.getLogger("sia_engine")

FEEDBACK_DB = os.path.expanduser("~/.hermes/runtime/sia_feedback.db")
SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query         TEXT,
    action        TEXT,
    result_snippet TEXT,
    rating        INTEGER,        -- 1~5
    timestamp     REAL
);
CREATE INDEX IF NOT EXISTS idx_feedback_action ON feedback_log(action);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback_log(rating);
"""


@dataclass
class FeedbackEntry:
    """단일 피드백 레코드."""
    query: str
    action: str
    result_snippet: str
    rating: int           # 1~5
    timestamp: float = 0.0


class SelfImprovingAgent:
    """
    사용자 피드백을 수집·분석하여 액션 품질을 자동 개선합니다.

    사용법:
        sia = SelfImprovingAgent()
        await sia.record_feedback(query="...", action="ask", result="...", rating=4)
        trends = await sia.analyze_trends()
        low = await sia.get_low_performers()
        suggestion = await sia.suggest_improvements("ask")
    """

    def __init__(self, db_path: str = FEEDBACK_DB, llm=None):
        self.db_path = db_path
        self.llm = llm  # 개선 제안 생성용 LLM 콜백
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    # ── DB 초기화 ───────────────────────────────────────

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SQL_SCHEMA)
        conn.commit()
        conn.close()
        log.info(f"[SIA] 피드백 DB 초기화: {self.db_path}")

    # ── 핵심 API ────────────────────────────────────────

    async def record_feedback(
        self,
        query: str,
        action: str,
        result: str,
        rating: int,        # 1~5
    ) -> int:
        """
        사용자 피드백을 SQLite에 기록.

        Args:
            query: 원래 사용자 질문
            action: 실행된 액션 ("web", "ask", "wiki", "tag", "exec")
            result: 실행 결과 문자열
            rating: 평점 (1=매우나쁨 ~ 5=매우좋음)

        Returns:
            feedback_log의 id (auto-increment)
        """
        if not 1 <= rating <= 5:
            raise ValueError(f"rating은 1~5 범위여야 합니다: {rating}")

        entry = FeedbackEntry(
            query=query[:500],
            action=action,
            result_snippet=result[:200] if result else "",
            rating=rating,
            timestamp=time.time(),
        )

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                """INSERT INTO feedback_log (query, action, result_snippet, rating, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (entry.query, entry.action, entry.result_snippet,
                 entry.rating, entry.timestamp),
            )
            conn.commit()
            feedback_id = cur.lastrowid
            log.info(f"[SIA] 피드백 기록: id={feedback_id} action={action} rating={rating}")
            return feedback_id
        finally:
            conn.close()

    async def analyze_trends(self, hours: int = 168) -> Dict[str, float]:
        """
        액션별 최근(hours) 평균 평점 분석.

        Returns:
            {"web": 4.2, "ask": 3.8, "tag": 4.5, "wiki": 4.1, "exec": 3.9}
        """
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT action, AVG(rating) as avg_rating, COUNT(*) as cnt
                   FROM feedback_log
                   WHERE timestamp > ?
                   GROUP BY action
                   ORDER BY avg_rating DESC""",
                (cutoff,),
            ).fetchall()
            return {row[0]: round(row[1], 2) for row in rows if row[1] is not None}
        finally:
            conn.close()

    async def get_low_performers(
        self,
        threshold: float = 3.0,
        min_samples: int = 3,
        hours: int = 168,
    ) -> List[str]:
        """
        평점이 threshold 이하인 저성능 액션 목록.

        Args:
            threshold: 이 값 이하의 평점을 저성능으로 간주 (기본 3.0)
            min_samples: 평가에 필요한 최소 샘플 수 (기본 3)
            hours: 분석 시간 범위 (기본 168h = 7일)

        Returns:
            ["ask", "exec"] — 개선이 필요한 액션명 리스트
        """
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT action, AVG(rating) as avg_rating, COUNT(*) as cnt
                   FROM feedback_log
                   WHERE timestamp > ?
                   GROUP BY action
                   HAVING cnt >= ? AND avg_rating <= ?""",
                (cutoff, min_samples, threshold),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    async def suggest_improvements(self, action: str) -> str:
        """
        특정 액션에 대한 개선 제안을 LLM으로 생성.

        Args:
            action: 개선할 액션명

        Returns:
            LLM이 생성한 개선 제안 문자열
        """
        # 저성능 피드백 샘플 수집
        conn = sqlite3.connect(self.db_path)
        try:
            poor_feedbacks = conn.execute(
                """SELECT query, rating FROM feedback_log
                   WHERE action = ? AND rating <= 2
                   ORDER BY timestamp DESC LIMIT 5""",
                (action,),
            ).fetchall()
        finally:
            conn.close()

        if not poor_feedbacks:
            return f"'{action}' 액션에 대한 저평가 피드백이 없습니다."

        samples = "\n".join(
            f"- query: {q[:80]!r} (rating: {r}/5)"
            for q, r in poor_feedbacks
        )

        if self.llm:
            prompt = (
                f"다음은 Hermes 시스템에서 '{action}' 액션이 낮은 평점을 받은 피드백 샘플입니다:\n\n"
                f"{samples}\n\n"
                f"이 액션의 성능을 개선할 방법을 2~3문장으로 제안해주세요. "
                f"구체적인 알고리즘 개선, 추가 검증 단계, 또는 Fallback 전략을 포함하세요."
            )
            try:
                import inspect
                result = await self.llm(prompt) if inspect.iscoroutinefunction(self.llm) else self.llm(prompt)
                return result.strip() if result else self._default_suggestion(action, samples)
            except Exception as e:
                log.debug(f"[SIA] LLM 개선 제안 실패: {e}")
                return self._default_suggestion(action, samples)
        else:
            return self._default_suggestion(action, samples)

    def _default_suggestion(self, action: str, samples: str) -> str:
        return (
            f"[{action}] 개선 제안:\n"
            f"최근 저평가 피드백 {len(samples.split(chr(10)))}건 발견.\n"
            f"- 정확도 향상: 추가 컨텍스트를 활용한 pre-filtering 도입\n"
            f"- Fallback 강화: 실패 시 2차 시도 또는 다른 액션으로 전환\n"
            f"- 사용자 피드백 패턴 분석: {samples[:100]}..."
        )

    # ── 유틸리티 ────────────────────────────────────────

    async def get_stats(self, hours: int = 168) -> Dict[str, Any]:
        """전체 피드백 통계."""
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(self.db_path)
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM feedback_log WHERE timestamp > ?",
                (cutoff,),
            ).fetchone()[0]
            avg = conn.execute(
                "SELECT AVG(rating) FROM feedback_log WHERE timestamp > ?",
                (cutoff,),
            ).fetchone()[0]
            return {
                "total_feedback": total,
                "average_rating": round(avg, 2) if avg else 0.0,
                "period_hours": hours,
            }
        finally:
            conn.close()

    async def get_recent_feedback(
        self, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """최근 피드백 목록 조회."""
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT query, action, result_snippet, rating, timestamp
                   FROM feedback_log ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [
                {
                    "query": r[0],
                    "action": r[1],
                    "result_snippet": r[2],
                    "rating": r[3],
                    "timestamp": r[4],
                }
                for r in rows
            ]
        finally:
            conn.close()

    async def reset_db(self):
        """테스트용: DB 초기화."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM feedback_log")
        conn.commit()
        conn.close()
        log.info("[SIA] DB 초기화 완료")
