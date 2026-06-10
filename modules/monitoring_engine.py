"""
monitoring_engine.py — MonitoringEngine v1.0 (Hermes v9.2)
================================================================
응답 품질, 에러율, 응답 시간을 실시간으로 추적 및 경고.

Priority 2 of v9.2 업그레이드:
  - record_metric(): 메트릭 기록 (SQLite + 메모리 캐시)
  - get_action_stats(): 액션별 통계
  - get_error_rate(): 전체 에러율
  - get_performance_trend(): 시간대별 성능 트렌드
  - alert_if_degradation(): 성능 저하 감지 알림
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("monitoring_engine")

METRICS_DB = os.path.expanduser("~/.hermes/runtime/metrics.db")
SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL,
    action          TEXT,
    response_time_ms REAL,
    result_length   INTEGER,
    error_flag      INTEGER,
    error_message   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_metric_action ON metric_log(action);
CREATE INDEX IF NOT EXISTS idx_metric_ts    ON metric_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_metric_error ON metric_log(error_flag);
"""

# 임계값 상수
WARN_ERROR_RATE      = 0.10   # 10% 이상 에러율 → 경고
WARN_RESPONSE_TIME   = 3000   # 3000ms 이상 평균 → 경고
WARN_ACTION_SUCCESS  = 0.80   # 80% 미만 성공률 → 경고


@dataclass
class MetricSnapshot:
    """단일 메트릭 레코드."""
    timestamp:        float
    action:           str
    response_time_ms: float
    result_length:    int
    error_flag:       bool   = False
    error_message:    str    = ""


class MonitoringEngine:
    """
    응답 품질, 에러율, 응답 시간을 실시간으로 추적합니다.

    사용법:
        mon = MonitoringEngine()
        await mon.record_metric(MetricSnapshot(...))
        stats = await mon.get_action_stats("web")
        alert = await mon.alert_if_degradation()
    """

    def __init__(self, db_path: str = METRICS_DB):
        self.db_path = db_path
        self._mem_cache: Dict[str, Dict[str, Any]] = {}  # 현재 24시간 메트릭 요약
        self._recent_snapshots: List[MetricSnapshot] = []  # 메모리 캐시 (최대 1000개)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    # ── DB 초기화 ───────────────────────────────────────

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SQL_SCHEMA)
        conn.commit()
        conn.close()
        log.info(f"[Monitoring] 메트릭 DB 초기화: {self.db_path}")

    # ── 핵심 API ────────────────────────────────────────

    async def record_metric(self, snapshot: MetricSnapshot) -> None:
        """
        메트릭을 SQLite에 기록하고 메모리 캐시를 업데이트합니다.

        Args:
            snapshot: 기록할 메트릭 스냅샷
        """
        # SQLite 기록
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO metric_log
                   (timestamp, action, response_time_ms, result_length, error_flag, error_message)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (snapshot.timestamp, snapshot.action, snapshot.response_time_ms,
                 snapshot.result_length, int(snapshot.error_flag), snapshot.error_message[:200]),
            )
            conn.commit()
        finally:
            conn.close()

        # 메모리 캐시 업데이트
        self._recent_snapshots.append(snapshot)
        if len(self._recent_snapshots) > 1000:
            self._recent_snapshots = self._recent_snapshots[-500:]

        log.debug(
            f"[Monitoring] 메트릭 기록: action={snapshot.action} "
            f"time={snapshot.response_time_ms:.0f}ms "
            f"error={snapshot.error_flag}"
        )

    async def get_action_stats(
        self, action: str, hours: int = 24
    ) -> Dict[str, Any]:
        """
        특정 액션의 통계를 조회합니다.

        Args:
            action: 액션명 ("web", "ask", "wiki", "tag", "exec")
            hours: 분석 시간 범위

        Returns:
            {
                "avg_response_time_ms": 850.0,
                "success_rate": 0.95,
                "error_rate": 0.05,
                "total_calls": 120,
                "avg_result_length": 320.0
            }
        """
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """SELECT
                       AVG(response_time_ms),
                       SUM(CASE WHEN error_flag=0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*),
                       SUM(CASE WHEN error_flag=1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*),
                       COUNT(*),
                       AVG(result_length)
                   FROM metric_log
                   WHERE action = ? AND timestamp > ?""",
                (action, cutoff),
            ).fetchone()

            if row and row[3] and row[3] > 0:
                return {
                    "action":             action,
                    "avg_response_time_ms": round(row[0], 1) if row[0] else 0.0,
                    "success_rate":        round(row[1], 3) if row[1] else 0.0,
                    "error_rate":          round(row[2], 3) if row[2] else 0.0,
                    "total_calls":         row[3],
                    "avg_result_length":   round(row[4], 0) if row[4] else 0,
                    "period_hours":        hours,
                }
            return {
                "action": action, "avg_response_time_ms": 0.0,
                "success_rate": 0.0, "error_rate": 0.0,
                "total_calls": 0, "avg_result_length": 0,
                "period_hours": hours,
            }
        finally:
            conn.close()

    async def get_all_action_stats(
        self, hours: int = 24
    ) -> Dict[str, Dict[str, Any]]:
        """모든 액션의 통계."""
        actions = ["ask", "web", "wiki", "tag", "exec", "file"]
        result = {}
        for action in actions:
            result[action] = await self.get_action_stats(action, hours)
        return result

    async def get_error_rate(self, hours: int = 24) -> float:
        """
        전체 에러율 (%).

        Returns:
            2.3 (2.3% 의미)
        """
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """SELECT
                       SUM(CASE WHEN error_flag=1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
                   FROM metric_log WHERE timestamp > ?""",
                (cutoff,),
            ).fetchone()
            return round(row[0], 2) if row and row[0] else 0.0
        finally:
            conn.close()

    async def get_performance_trend(
        self, hours: int = 24, interval_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        시간대별 성능 트렌드 (차트 데이터용).

        Args:
            hours: 분석 시간 범위
            interval_minutes: 집계 간격 (분)

        Returns:
            {
                "labels": ["14:00", "15:00", ...],
                "response_times": [850, 920, ...],
                "error_rates": [0.02, 0.03, ...],
                "call_counts": [12, 8, ...]
            }
        """
        cutoff = time.time() - (hours * 3600)
        interval_seconds = interval_minutes * 60

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT
                       CAST((CAST(timestamp AS INTEGER) - ?) / ? AS INTEGER) AS slot,
                       AVG(response_time_ms),
                       SUM(CASE WHEN error_flag=1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*),
                       COUNT(*)
                   FROM metric_log
                   WHERE timestamp > ?
                   GROUP BY slot
                   ORDER BY slot""",
                (int(cutoff), interval_seconds, cutoff),
            ).fetchall()

            labels = []
            resp_times = []
            err_rates = []
            call_counts = []

            for slot, avg_rt, err_pct, cnt in rows:
                slot_time = cutoff + (slot * interval_seconds)
                import datetime
                labels.append(
                    datetime.datetime.fromtimestamp(slot_time).strftime("%H:%M")
                )
                resp_times.append(round(avg_rt, 1) if avg_rt else 0.0)
                err_rates.append(round(err_pct, 2) if err_pct else 0.0)
                call_counts.append(cnt)

            return {
                "labels": labels,
                "response_times": resp_times,
                "error_rates": err_rates,
                "call_counts": call_counts,
            }
        finally:
            conn.close()

    async def alert_if_degradation(self, hours: int = 1) -> Optional[str]:
        """
        성능 저하 감지 — 에러율/응답시간/성공률 체크.

        규칙:
          - 에러율 > 10% → ⚠️ 경고
          - 응답시간 > 3초 평균 → ⚠️ 경고
          - 특정 액션 성공률 < 80% → ⚠️ 경고

        Returns:
            경고 메시지 문자열, 또는 None (이상 없음)
        """
        warnings: List[str] = []

        # 1. 전체 에러율
        err_rate = await self.get_error_rate(hours)
        if err_rate > WARN_ERROR_RATE * 100:
            warnings.append(
                f"⚠️ 전체 에러율 {err_rate:.1f}% — 임계값 {WARN_ERROR_RATE*100:.0f}% 초과"
            )

        # 2. 액션별 체크
        all_stats = await self.get_all_action_stats(hours)
        for action, stats in all_stats.items():
            if stats["total_calls"] == 0:
                continue

            # 응답시간
            if stats["avg_response_time_ms"] > WARN_RESPONSE_TIME:
                warnings.append(
                    f"⚠️ [{action}] 평균 응답시간 {stats['avg_response_time_ms']:.0f}ms "
                    f"— 임계값 {WARN_RESPONSE_TIME}ms 초과"
                )

            # 성공률
            if stats["success_rate"] < WARN_ACTION_SUCCESS:
                warnings.append(
                    f"⚠️ [{action}] 성공률 {stats['success_rate']*100:.0f}% "
                    f"— 임계값 {WARN_ACTION_SUCCESS*100:.0f}% 미만"
                )

        if not warnings:
            return None

        header = f"📊 **성능 저하 감지** (최근 {hours}시간)\n"
        return header + "\n".join(warnings[:5])  # 최대 5개 경고

    # ── 유틸리티 ────────────────────────────────────────

    async def get_summary(self, hours: int = 24) -> Dict[str, Any]:
        """전체 모니터링 요약."""
        return {
            "error_rate": await self.get_error_rate(hours),
            "action_stats": await self.get_all_action_stats(hours),
            "degradation": await self.alert_if_degradation(hours),
            "period_hours": hours,
        }

    async def reset_db(self):
        """테스트용: DB 초기화."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM metric_log")
        conn.commit()
        conn.close()
        log.info("[Monitoring] DB 초기화 완료")
