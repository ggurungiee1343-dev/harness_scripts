"""mayor_agent.py — Mayor 에이전트 (Stage 5 루프 감독자, 2026-06-09)

Boris Cherny "루프 설계" 아키텍처의 Stage 5 구현:
"루프가 루프를 감독한다" — Mayor가 모든 활성 루프의 상태를 추적하고
비용/반복수/정체(stagnation)를 감시한다.

사용법:
    from modules.mayor_agent import mayor
    loop_id = mayor.register("에이전틱루프", max_iters=3)
    mayor.tick(loop_id, tokens_used=500)
    mayor.finish(loop_id, success=True)
    summary = mayor.dashboard()

텔레그램: /orchestrate mayor  → 대시보드 출력
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

# ── 예산 상수 ────────────────────────────────────────────────
DEFAULT_MAX_ITERS   = 3       # 루프당 기본 최대 반복 수
DEFAULT_TOKEN_BUDGET = 12000  # 루프당 기본 토큰 예산 (추정치 기반)
STALE_TIMEOUT       = 300     # 5분 이상 tick 없으면 "정체" 경고


@dataclass
class LoopState:
    loop_id: str
    name: str
    max_iters: int
    token_budget: int
    started_at: float = field(default_factory=time.monotonic)
    last_tick_at: float = field(default_factory=time.monotonic)
    iters: int = 0
    tokens_used: int = 0
    status: str = "running"   # running | done | budget_exceeded | stale
    result_summary: str = ""

    def is_stale(self) -> bool:
        return (time.monotonic() - self.last_tick_at) > STALE_TIMEOUT and self.status == "running"

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def budget_pct(self) -> int:
        if self.token_budget <= 0:
            return 0
        return int(self.tokens_used / self.token_budget * 100)


class MayorAgent:
    """Mayor — 여러 루프의 생애주기(등록→틱→종료)를 감독하는 단일 관제탑."""

    def __init__(self):
        self._loops: Dict[str, LoopState] = {}

    # ── 루프 생애주기 API ──────────────────────────────────────

    def register(
        self,
        name: str,
        max_iters: int = DEFAULT_MAX_ITERS,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ) -> str:
        """새 루프 등록. loop_id 반환."""
        loop_id = str(uuid.uuid4())[:8]
        self._loops[loop_id] = LoopState(
            loop_id=loop_id,
            name=name,
            max_iters=max_iters,
            token_budget=token_budget,
        )
        print(f"👑 [Mayor] 루프 등록: [{loop_id}] {name} (max_iters={max_iters}, budget={token_budget}토큰)")
        return loop_id

    def tick(self, loop_id: str, tokens_used: int = 0) -> bool:
        """반복 1회 기록. 예산/반복수 초과 시 False 반환 → 루프 중단 신호."""
        state = self._loops.get(loop_id)
        if not state or state.status != "running":
            return False

        state.iters += 1
        state.tokens_used += tokens_used
        state.last_tick_at = time.monotonic()

        if state.iters > state.max_iters:
            state.status = "budget_exceeded"
            state.result_summary = f"max_iters({state.max_iters}) 초과"
            print(f"⛔ [Mayor] [{loop_id}] {state.name} — 반복 한도 초과")
            return False

        if state.tokens_used > state.token_budget:
            state.status = "budget_exceeded"
            state.result_summary = f"token_budget({state.token_budget}) 초과 ({state.tokens_used}토큰 사용)"
            print(f"⛔ [Mayor] [{loop_id}] {state.name} — 토큰 예산 초과")
            return False

        return True

    def finish(self, loop_id: str, success: bool = True, summary: str = ""):
        """루프 정상 종료 등록."""
        state = self._loops.get(loop_id)
        if not state:
            return
        state.status = "done" if success else "failed"
        state.result_summary = summary or ("완료" if success else "실패")
        elapsed = f"{state.elapsed_s():.1f}s"
        print(f"✅ [Mayor] [{loop_id}] {state.name} — {state.status} ({elapsed}, {state.iters}회, {state.tokens_used}토큰)")

    def should_stop(self, loop_id: str) -> bool:
        """Mayor가 이 루프를 중단해야 한다고 판단하면 True."""
        state = self._loops.get(loop_id)
        if not state:
            return True
        if state.status in ("budget_exceeded", "failed"):
            return True
        if state.is_stale():
            state.status = "stale"
            print(f"⚠️ [Mayor] [{loop_id}] {state.name} — {STALE_TIMEOUT}초 정체 감지")
            return True
        return False

    def get_stop_reason(self, loop_id: str) -> str:
        state = self._loops.get(loop_id)
        if not state:
            return "알 수 없는 루프"
        return state.result_summary or state.status

    # ── 대시보드 ───────────────────────────────────────────────

    def dashboard(self, include_done: bool = False) -> str:
        """현재 루프 상태 대시보드 (텔레그램 출력용)."""
        loops = [s for s in self._loops.values()]
        if not include_done:
            loops = [s for s in loops if s.status not in ("done", "failed")]
        if not loops:
            return "👑 **Mayor 대시보드** — 활성 루프 없음"

        lines = ["👑 **Mayor 대시보드**", ""]
        for s in loops:
            status_icon = {
                "running": "🔄",
                "done": "✅",
                "failed": "❌",
                "budget_exceeded": "⛔",
                "stale": "⚠️",
            }.get(s.status, "❓")

            stale_warn = " ⚠️ 정체!" if s.is_stale() else ""
            lines.append(
                f"{status_icon} **[{s.loop_id}]** {s.name}\n"
                f"   반복: {s.iters}/{s.max_iters}회 | "
                f"토큰: {s.tokens_used}/{s.token_budget} ({s.budget_pct()}%) | "
                f"경과: {s.elapsed_s():.0f}s{stale_warn}"
            )
            if s.result_summary:
                lines.append(f"   └ {s.result_summary}")
        return "\n".join(lines)

    def status_for_loop(self, loop_id: str) -> dict:
        """단일 루프 상태 dict 반환 (로그용)."""
        s = self._loops.get(loop_id)
        if not s:
            return {}
        return {
            "id": s.loop_id,
            "name": s.name,
            "iters": s.iters,
            "tokens_used": s.tokens_used,
            "status": s.status,
            "elapsed_s": round(s.elapsed_s(), 1),
        }

    def prune_old(self, max_keep: int = 20):
        """완료된 오래된 루프 정리 (메모리 관리)."""
        done_ids = [lid for lid, s in self._loops.items() if s.status in ("done", "failed")]
        for lid in done_ids[:-max_keep]:
            del self._loops[lid]


# ── 싱글톤 인스턴스 (harness_agent, orchestrator에서 import) ──
mayor = MayorAgent()
