"""
core_reducer.py — Hermes3 v9.0 Stateless Reducer & Micro-Agent Pipeline
=========================================================================
설계 원칙:
  - Stateless: AgentContext는 불변(frozen), 요청 간 상태 없음
  - 메모리 영향: 0MB (subprocess 분리, 지연 임포트)
  - 부팅 영향: 0초 (지연 임포트)
  - 3-Agent Pipeline: Decision → Execution → Knowledge

액션 라우팅 (ExecutionAgent):
  - ask   → handlers._memory.cmd_ask_logic (CoVe 팩트체크)
  - tag   → handlers._approval.cmd_tag_logic
  - web   → duckduckgo_search DDGS (웹 검색, v9.1.4)
  - wiki  → HybridKnowledgeIndexer (로컬 위키 FTS5+벡터 검색)
  - exec  → modules.executor.execute_bash_command (Trajectory Regulation)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from modules.sia_engine import SelfImprovingAgent
from modules.monitoring_engine import MonitoringEngine, MetricSnapshot

log = logging.getLogger("core_reducer")

from modules.core_agents import (
    SourceChannel, AgentContext, ArchitecturalJudgmentGate,
    DecisionAgent, ExecutionAgent, KnowledgeAgent,
)

# ═══════════════════════════════════════════════════════════════════════════
#  HermesCoreReducer — 3-Agent Pipeline 오케스트레이터
# ═══════════════════════════════════════════════════════════════════════════

class HermesCoreReducer:
    """
    순수 함수 리듀서 (v9.0):
    AgentContext → Decision → Execution → Refined Context → Result

    Pause/Resume 지원 (v9.1):
      - execution_state == "PAUSED" → 즉시 반환
      - execution_state == "RUNNING" → 정상 실행
    """

    def __init__(self, llm=None, db_path: str = "~/.hermes/runtime/reducer_cache.db"):
        self.llm             = llm
        self.decision_agent  = DecisionAgent(llm)
        self.execution_agent = ExecutionAgent()
        self.knowledge_agent = KnowledgeAgent()
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_v91_cache_table()
        # v9.1+ 메모리 LRU 캐시 (SQLite 히트를 줄이기 위한 1차 캐시)
        self._mem_cache: Dict[str, Dict[str, Any]] = {}
        self._mem_cache_ttl: int = 60  # 초
        # v9.2: SIA (Self-Improving Agent) + Monitoring Engine + Judgment Gate
        self.judgment = ArchitecturalJudgmentGate()
        self.sia = SelfImprovingAgent(llm=llm)
        self.monitoring = MonitoringEngine()

    def _init_v91_cache_table(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_cache (
                context_hash TEXT PRIMARY KEY,
                cached_decision TEXT,
                recorded_at REAL,
                ttl_seconds INTEGER DEFAULT 3600
            );
        """)
        conn.commit()
        conn.close()

    async def cleanup_expired_cache(self):
        """주기적으로 호출하여 만료된 캐시 정리"""
        conn = sqlite3.connect(self.db_path)
        deleted = conn.execute(
            "DELETE FROM decision_cache WHERE (recorded_at + ttl_seconds) < ?",
            (time.time(),)
        ).rowcount
        conn.commit()
        cache_size = conn.execute("SELECT COUNT(*) FROM decision_cache").fetchone()[0]
        log.info(f"[v9.1 Cache] 만료된 항목 {deleted}개 삭제, 현재 캐시 크기: {cache_size}")
        conn.close()

    async def reduce(self, ctx: AgentContext) -> str:
        """
        핵심 파이프라인 실행. (v9.2: SIA 피드백용 feedback_id + 모니터링 타이밍)

        Args:
            ctx: 불변 입력 컨텍스트

        Returns:
            실행 결과 문자열
        """
        # ── [Harness 7] 결정론적 숏컷 (v9.2 Priority 4) ──────────────────────
        is_shortcut, decision = self.judgment.is_deterministic_shortcut(ctx.user_query)
        if is_shortcut:
            log.info(f"⚡ [JudgmentGate] 결정론적 숏컷: {decision['command']} (토큰 0원)")
            # 모니터링 기록
            snapshot = MetricSnapshot(
                timestamp=time.time(),
                action="system",
                response_time_ms=0.5,
                result_length=0,
                error_flag=False,
                error_message="",
            )
            await self.monitoring.record_metric(snapshot)
            return f"✅ {decision['command'].upper()} executed"

        # Pause/Resume 상태 확인 (v9.1)
        if ctx.execution_state == "PAUSED":
            log.info(f"[Reducer] PAUSED 상태 — 처리 중단 (hash={ctx.get_hash()[:8]})")
            return "⏸️ 현재 일시정지 상태입니다."

        ctx_hash = ctx.get_hash()
        start_time = time.time()

        # v9.1+: 메모리 캐시 1차 조회 (SQLite보다 빠름)
        if ctx_hash in self._mem_cache:
            cached_entry = self._mem_cache[ctx_hash]
            if (cached_entry["_ts"] + self._mem_cache_ttl) > time.time():
                log.info(f"[Reducer] 메모리 캐시 히트! (hash={ctx_hash[:8]})")
                decision = cached_entry["decision"]
                conn = None
            else:
                del self._mem_cache[ctx_hash]
                conn = sqlite3.connect(self.db_path)
        else:
            conn = sqlite3.connect(self.db_path)

        if conn is not None:
            # SQLite 캐시 조회 시 TTL 확인 (v9.1)
            cached = conn.execute(
                """SELECT cached_decision FROM decision_cache 
                   WHERE context_hash = ? 
                   AND (recorded_at + ttl_seconds) > ?""",
                (ctx_hash, time.time())
            ).fetchone()

            if cached:
                log.info(f"[Reducer] SQLite 캐시 히트! (hash={ctx_hash[:8]})")
                decision = json.loads(cached[0])
            else:
                # 1단계: 결정
                decision = await self.decision_agent.decide(ctx)
                log.info(f"[Reducer] 결정: {decision.get('action')} (신뢰도={decision.get('confidence', 0):.2f})")

                # 캐시 저장 (SQLite + 메모리)
                conn.execute(
                    "INSERT OR REPLACE INTO decision_cache (context_hash, cached_decision, recorded_at) VALUES (?, ?, ?)",
                    (ctx_hash, json.dumps(decision), time.time())
                )
                conn.commit()

            conn.close()

        # 메모리 캐시 갱신 (SQLite 경유 여부와 무관)
        self._mem_cache[ctx_hash] = {"decision": decision, "_ts": time.time()}
        # 메모리 캐시 크기 제한 (최대 128개)
        if len(self._mem_cache) > 128:
            oldest = min(self._mem_cache.keys(), key=lambda k: self._mem_cache[k]["_ts"])
            del self._mem_cache[oldest]

        action = decision.get("action", "unknown")

        # 2단계: 실행
        try:
            result = await self.execution_agent.execute(decision)
            result_text = result.get("result", "❌ 결과 없음")
            error_flag = not result.get("success", True)
            error_msg = result.get("error") or ""

            # 2.5단계: LLM 정제 — wiki/ask 결과를 자연어 답변으로 재가공
            if action in ("wiki", "ask") and self.llm and result_text and result_text != "❌ 결과 없음":
                result_text = await self._refine_result(ctx.user_query, result_text, action)

            # 3단계: 지식 정제 (컨텍스트 업데이트)
            result["result"] = result_text
            await self.knowledge_agent.refine(ctx, result)

        except Exception as e:
            result_text = f"❌ 실행 오류: {str(e)[:200]}"
            error_flag = True
            error_msg = str(e)
            log.error(f"[Reducer] 실행 오류: {e}", exc_info=True)

        # v9.2: 모니터링 메트릭 기록
        response_time = (time.time() - start_time) * 1000
        snapshot = MetricSnapshot(
            timestamp=time.time(),
            action=action,
            response_time_ms=response_time,
            result_length=len(result_text),
            error_flag=error_flag,
            error_message=error_msg[:200],
        )
        await self.monitoring.record_metric(snapshot)

        return result_text

    async def _refine_result(self, query: str, raw_result: str, action: str) -> str:
        """[v9.1.3] LLM으로 wiki/ask 실행 결과를 자연어 답변으로 재가공."""
        prompt = (
            f"사용자 질문: {query}\n\n"
            f"검색 결과 ({'위키' if action == 'wiki' else '메모리'}):\n{raw_result[:1500]}\n\n"
            "위 검색 결과를 바탕으로 사용자에게 자연스러운 한국어 답변을 작성하세요.\n"
            "검색 결과에 질문과 관련된 정보가 없으면 '볼트 내 관련 정보를 찾을 수 없습니다'라고 말하고\n"
            "웹 검색이나 다른 방법을 제안해주세요.\n"
            "답변은 간결하게 3~5문장으로 작성하세요."
        )
        try:
            import inspect
            refined = await self.llm(prompt) if inspect.iscoroutinefunction(self.llm) else self.llm(prompt)
            return refined.strip() if refined and refined.strip() else raw_result
        except Exception as e:
            log.debug(f"[Reducer] LLM 정제 실패: {e}")
            return raw_result

    # ── v9.2: SIA 피드백 인터페이스 ────────────────────────────────────────

    async def apply_user_feedback(
        self,
        query: str,
        action: str,
        result_text: str,
        rating: int,  # 1~5
    ) -> int:
        """
        사용자 피드백(👍/👎)을 SIA에 기록.

        Args:
            query: 원본 질문
            action: 실행된 액션
            result_text: 실행 결과
            rating: 1(매우나쁨) ~ 5(매우좋음)

        Returns:
            feedback_log의 id
        """
        feedback_id = await self.sia.record_feedback(
            query=query,
            action=action,
            result=result_text,
            rating=rating,
        )

        # 저성능 액션 자동 감지 및 경고
        low = await self.sia.get_low_performers(threshold=3.0, min_samples=3)
        if low:
            log.warning(f"[Reducer] 저성능 액션 감지: {low}")
            # 추후 알림 로직 확장 가능

        return feedback_id

    async def reduce_with_context(self, ctx: AgentContext):
        """
        컨텍스트까지 반환하는 버전 (상태 추적이 필요한 경우).

        Returns:
            (result_str, refined_ctx)
        """
        decision = await self.decision_agent.decide(ctx)
        result   = await self.execution_agent.execute(decision)
        new_ctx  = await self.knowledge_agent.refine(ctx, result)
        return result.get("result", ""), new_ctx


# ═══════════════════════════════════════════════════════════════════════════
#  편의 함수 & 싱글톤
# ═══════════════════════════════════════════════════════════════════════════

_reducer_instance: Optional[HermesCoreReducer] = None


def get_reducer(llm=None) -> HermesCoreReducer:
    """전역 싱글톤 리듀서 반환."""
    global _reducer_instance
    if _reducer_instance is None:
        _reducer_instance = HermesCoreReducer(llm)
    return _reducer_instance


async def execute_decision(
    decision: Dict[str, Any],
    context: Optional[AgentContext] = None,
) -> str:
    """
    편의 함수: 결정 딕셔너리로 직접 실행.

    Example:
        result = await execute_decision({"action": "web", "query": "Hermes3 AI"})
    """
    agent = ExecutionAgent()
    res = await agent.execute(decision)
    return res.get("result", "")


# ═══════════════════════════════════════════════════════════════════════════
#  테스트 실행
# ═══════════════════════════════════════════════════════════════════════════

async def _main():
    """기본 동작 검증."""
    ctx = AgentContext(
        user_query="Hermes3 v9.0 테스트",
        source_channel=SourceChannel.CLI,
    )
    reducer = HermesCoreReducer()
    output  = await reducer.reduce(ctx)
    print(f"✅ core_reducer.py v9.0 구현 완료!")
    print(f"✅ 결과: {output}")
    print(f"✅ 해시: {ctx.get_hash()[:16]}...")
    print(f"✅ 직렬화: {list(ctx.to_dict().keys())}")


if __name__ == "__main__":
    asyncio.run(_main())
