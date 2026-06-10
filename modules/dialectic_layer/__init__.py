"""
Dialectic Layer 패키지 — 3단계 분리 (Deriver / Dialectic / Dreamer)
===================================================================
Life-Harness Layer 4: 전략적 메모리 회상/연결 엔진

주요 기능:
- 크로스 세션 컨텍스트 연결 (session_search 기반)
- 의사결정 근거 회상 (Decision Justification)
- 우선순위 기반 회상 스케줄링
- 메모리 충돌 감지 및 조정
- 의미론적 메모리 연결 (스킬 + 바이오메모리 + 세션 로그)
- Honcho 스타일 사용자 페르소나 누적

arXiv 2605.22166 — Memory Dialectic Architecture

※ 모든 심볼을 최상위로 재export하여 backward compatibility 유지
"""

# ── _base: 경로/DB 공유 ────────────────────────────────
from ._base import _init_db, _migrate
from ._base import HERMES_HOME, MEMORY_DIR, DIALECTIC_DB, SESSION_LOG, DECISIONS_DIR

# ── _deriver: 의사결정 계층 ─────────────────────────────
from ._deriver import record_decision, recall_decision, detect_memory_conflicts

# ── _dialectic: 메모리 연결 & 컨텍스트 ──────────────────
from ._dialectic import (
    link_memories,
    get_linked_memories,
    save_context_summary,
    recall_relevant_context,
    auto_link_from_session_logs,
)

# ── _dreamer: 사용자 페르소나 & 선호도 ──────────────────
from ._dreamer import (
    TRAIT_CATEGORIES,
    extract_traits_from_text,
    update_user_persona,
    get_user_persona,
    get_persona_summary,
    record_preference,
    get_preference,
    get_all_preferences,
    sync_style_profile,
)

# ── 마이그레이션 (모듈 로드 시 실행) ───────────────────
_migrate()

# ── _handoff: 세션 핸드오프 ────────────────────────────
from ._handoff import (
    save_handoff,
    load_handoff,
    get_latest_handoff,
    list_handoffs,
    delete_handoff,
)

# ── _fts: FTS5 전문 검색 ──────────────────────────────
from ._fts import (
    search_all,
    search_summaries,
    search_decisions,
    search_handoffs,
    rebuild_index,
)
