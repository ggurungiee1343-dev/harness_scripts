"""
Harness V2.5 — Hermes Bio-Memory Patch (3계층 통합 파사드)
============================================================
Life-Harness Layer 2~3: Deriver + Dialectic + Dreamer 통합

주요 기능:
- bio_add_message: L1→L2 승격 요청 (Deriver Layer)
- cmd_memory: 종합 메모리 상태 출력
- cmd_memory_search: L2/L3 통합 검색
- cmd_memory_dream: Dreaming 파이프라인 트리거
- cmd_memory_audit: 메모리 감사 함수 (Layer/Source/Expiry)
- get_enriched_context: Dialectic Layer 기반 컨텍스트 보강

아키텍처:
  Deriver   (deriver_layer.py)  — L1 Working → L2 Episodic
  Dialectic (dialectic_layer.py) — 전략적 회상/연결 (독립 모듈)
  Dreamer   (dreamer_layer.py)  — L2→L3 Semantic + Dreaming
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger("HermesMemoryPatch")

# ── 레이어 임포트 ──────────────────────────────────────────
from modules.deriver_layer import DeriverEngine, ImportanceScorer, ForgettingCurve
from modules.dreamer_layer import DreamerEngine
import modules.dialectic_layer as dialectic

# ── 전역 인스턴스 ──────────────────────────────────────────
_deriver = None
_dreamer = None


# ═══════════════════════════════════════════════════════════
# 초기화
# ═══════════════════════════════════════════════════════════

def init_bio_memory():
    """
    모든 메모리 레이어 초기화.
    hermes_local.py의 _load_module 호출 시 1회 실행됨.
    """
    global _deriver, _dreamer
    _deriver = DeriverEngine()
    _dreamer = DreamerEngine()
    logger.info("🧠 [Memory Patch] 3계층 메모리 시스템 초기화 완료 (Deriver + Dialectic + Dreamer)")
    return True


def _ensure_initialized():
    """레이어가 초기화되지 않았으면 자동 초기화."""
    global _deriver, _dreamer
    if _deriver is None or _dreamer is None:
        init_bio_memory()


# ═══════════════════════════════════════════════════════════
# L1→L2 승격
# ═══════════════════════════════════════════════════════════

def bio_add_message(role: str, content: str):
    """
    L1→L2 기억 승격 진입점.
    hermes_handlers.py에서 /ask 등 명령어 실행 시 호출됨.
    """
    _ensure_initialized()
    _deriver.add_message(role, content)


# ═══════════════════════════════════════════════════════════
# /memory — 종합 상태 출력
# ═══════════════════════════════════════════════════════════

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모리 시스템 종합 상태 + 액션 버튼 출력."""
    _ensure_initialized()

    # Deriver 상태
    ds = _deriver.get_status()

    # Dreamer 상태
    ds_text = _dreamer.get_status_text()

    # L2 연상망 정보
    l2 = _deriver.get_l2_data()
    associations = l2.get("associations", {})
    edge_count = sum(len(targets) for targets in associations.values()) // 2
    forget_candidates = ds.get("forget_candidates", 0)

    # Dialectic 결정 수
    try:
        conn = dialectic._init_db()
        dec_count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        summary_count = conn.execute("SELECT COUNT(*) FROM context_summaries").fetchone()[0]
        conn.close()
    except Exception:
        dec_count = 0
        summary_count = 0

    message = (
        "🧠 **Harness Bio-Memory 실시간 가동 현황**\n\n"
        "**━ Layer 1 — Deriver (L1~L2 파생)** ━\n"
        f"• L1 Working Memory: `{ds['l1_count']}/{ds['l1_max']} items`\n"
        f"• L2 Episodic Cache: `{ds['l2_count']}/{ds['l2_max']} eps`\n"
        f"• ↳ 연상망 엣지: `{edge_count}개`\n"
        f"• ↳ 소멸 직전: `{forget_candidates}개` 항목\n"
        f"• SemanticEngine: {'✅ ON' if ds['sem_engine'] else '⚠️ OFF'}\n\n"
        "**━ Layer 2 — Dialectic (전략 회상)** ━\n"
        f"• 의사결정 기록: `{dec_count}건`\n"
        f"• 컨텍스트 요약: `{summary_count}건`\n\n"
        "**━ Layer 3 — Dreamer (L3 통합)** ━\n"
        f"• {ds_text}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔍 검색", callback_data="mem_search"),
            InlineKeyboardButton("🌙 Dreaming", callback_data="mem_dream"),
        ],
        [
            InlineKeyboardButton("📋 감사", callback_data="mem_audit"),
            InlineKeyboardButton("💾 저장 (중요)", callback_data="mem_save"),
        ],
        [InlineKeyboardButton("❌ 닫기", callback_data="mem_close")],
    ]
    await update.message.reply_text(
        message, reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ═══════════════════════════════════════════════════════════
# /memory_search — 통합 검색
# ═══════════════════════════════════════════════════════════

async def cmd_memory_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """L2 에피소드 + Dialectic 의사결정 통합 검색."""
    _ensure_initialized()

    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "🔍 사용법: `/memory_search <검색어>`\n"
            "예: `/memory_search 트레이딩`"
        )
        return

    # 1. L2 에피소드 검색 (Deriver)
    l2 = _deriver.get_l2_data()
    episodes = l2.get("episodes", [])
    results = []

    # 키워드 매칭
    query_kws = set(query.split())
    for ep in episodes:
        content = ep.get("content", "")
        ep_kws = set(ep.get("keywords", []))
        kw_score = len(query_kws & ep_kws) * 3
        content_score = sum(1 for kw in query_kws if kw.lower() in content.lower()) * 2
        tag_score = sum(1 for kw in query_kws for tag in ep.get("context_tags", []) if kw.lower() in tag.lower()) * 2
        total = kw_score + content_score + tag_score
        if total > 0:
            retention = ForgettingCurve.retention(
                ep.get("importance", 1.0),
                ep.get("last_accessed", ep.get("timestamp", "")),
            )
            results.append((total, ep, retention))

    results.sort(key=lambda x: x[0], reverse=True)
    top = results[:8]

    # 2. Dialectic 의사결정 검색
    dec_results = dialectic.recall_decision(query, limit=3)

    lines = [f"🔍 **메모리 통합 검색** (쿼리: `{query}`)\n"]

    # 에피소드 결과
    if top:
        lines.append("━ **L2 에피소드** ━")
        for score, ep, retention in top:
            ts = ep.get("timestamp", "")[:16].replace("T", " ")
            content = ep.get("content", "")[:120]
            tags = ", ".join(ep.get("context_tags", []))
            tag_str = f" [{tags}]" if tags else ""
            lines.append(
                f"• [{ts}] ⭐{ep.get('importance', 0):.1f} 보유:{retention:.0%}{tag_str}\n"
                f"  `{content}...`\n"
            )
    else:
        lines.append("• L2: 일치하는 에피소드 없음\n")

    # 의사결정 결과
    if dec_results:
        lines.append("━ **Dialectic 의사결정** ━")
        for dec in dec_results:
            ts = dec.get("timestamp", "")[:16].replace("T", " ")
            lines.append(f"• [{ts}] **{dec['title'][:40]}** — {dec['outcome'][:60]}")

    keyboard = [[InlineKeyboardButton("❌ 닫기", callback_data="mem_close")]]
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ═══════════════════════════════════════════════════════════
# /memory_dream — Dreaming 트리거
# ═══════════════════════════════════════════════════════════

async def cmd_memory_dream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dreaming 파이프라인 즉시 실행."""
    _ensure_initialized()

    await update.message.reply_text("🌙 **Dreaming 세션 시작 중...** 잠시만 기다려주세요~")

    try:
        # LLM 함수 (hybrid_router 사용)
        from hybrid_router import router

        async def llm_func(prompt):
            res, name = router.send_completion(prompt)
            return res, name

        result = await _dreamer.dream(history_data=[], llm_func=llm_func)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ Dreaming 실행 실패: {e}")


# ═══════════════════════════════════════════════════════════
# /memory_audit — 메모리 감사 함수 (Layer/Source/Expiry)
# ═══════════════════════════════════════════════════════════

async def cmd_memory_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모리 3계층 종합 감사 리포트."""
    _ensure_initialized()

    try:
        audit_lines = ["📋 **메모리 시스템 종합 감사 리포트**\n"]

        # ── Layer 감사 ─────────────────────────────────
        audit_lines.append("━ **🧩 Layer 감사** ━")

        # L1 파일
        l1_path = _deriver.l1_path
        if l1_path.exists():
            l1 = json.loads(l1_path.read_text(encoding="utf-8"))
            l1_size = l1_path.stat().st_size
            audit_lines.append(f"• L1 (Working): `{len(l1)}개` 저장소 크기: `{l1_size:,} bytes`")
        else:
            audit_lines.append("• ⚠️ L1 (Working): 파일 없음")

        # L2 파일
        l2_path = _deriver.l2_path
        if l2_path.exists():
            l2_size = l2_path.stat().st_size
            l2 = _deriver.get_l2_data()
            audit_lines.append(f"• L2 (Episodic): `{len(l2.get('episodes', []))}개` 저장소 크기: `{l2_size:,} bytes`")
        else:
            audit_lines.append("• ⚠️ L2 (Episodic): 파일 없음")

        # L3 파일
        l3_path = Path("/Users/bluesea/.hermes/memory/semantic_memory.json")
        if l3_path.exists():
            l3_size = l3_path.stat().st_size
            l3 = json.loads(l3_path.read_text(encoding="utf-8"))
            audit_lines.append(f"• L3 (Semantic): `{len(l3.get('patterns', []))} 패턴` `{len(l3.get('procedural', {}))} 절차` 저장소 크기: `{l3_size:,} bytes`")
        else:
            audit_lines.append("• ⚠️ L3 (Semantic): 파일 없음")

        # Dialectic DB
        db_path = Path("/Users/bluesea/.hermes/dialectic.db")
        if db_path.exists():
            db_size = db_path.stat().st_size
            audit_lines.append(f"• Dialectic DB: `{db_size:,} bytes`")
        else:
            audit_lines.append("• ⚠️ Dialectic DB: 파일 없음")

        # ── 만료 감사 (Expiry) ─────────────────────────
        audit_lines.append("\n━ **⏰ 만료 감사 (Expiry)** ━")

        l2 = _deriver.get_l2_data()
        episodes = l2.get("episodes", [])
        near_expiry = []
        for ep in episodes:
            retention = ForgettingCurve.retention(
                ep.get("importance", 1.0),
                ep.get("last_accessed", ep.get("timestamp", "")),
            )
            if retention < 0.3:
                near_expiry.append((retention, ep))

        if near_expiry:
            audit_lines.append(f"• ⚠️ 소멸 임박 ({len(near_expiry)}개, 보유율 30% 미만):")
            for ret, ep in near_expiry[:5]:
                ts = ep.get("timestamp", "")[:16].replace("T", " ")
                audit_lines.append(f"  - [{ts}] 보유율 `{ret:.0%}` ⭐{ep.get('importance', 0):.1f}")
        else:
            audit_lines.append("• ✅ 소멸 임박 항목 없음 (모두 양호)")

        # ── 출처 감사 (Source) ─────────────────────────
        audit_lines.append("\n━ **🔍 출처 감사 (Source)** ━")
        role_count = {}
        for ep in episodes:
            role = ep.get("role", "unknown")
            role_count[role] = role_count.get(role, 0) + 1
        for role, count in sorted(role_count.items(), key=lambda x: x[1], reverse=True):
            audit_lines.append(f"• {role}: `{count}개`")

        # 태그 분포
        tag_count = {}
        for ep in episodes:
            for tag in ep.get("context_tags", []):
                tag_count[tag] = tag_count.get(tag, 0) + 1
        if tag_count:
            audit_lines.append("\n**태그 분포:**")
            for tag, count in sorted(tag_count.items(), key=lambda x: x[1], reverse=True):
                audit_lines.append(f"  • {tag}: `{count}개`")

        keyboard = [[InlineKeyboardButton("❌ 닫기", callback_data="mem_close")]]
        await update.message.reply_text(
            "\n".join(audit_lines), reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        await update.message.reply_text(f"❌ 메모리 감사 실행 실패: {e}")


# ═══════════════════════════════════════════════════════════
# 컨텍스트 보강 (Dialectic 기반)
# ═══════════════════════════════════════════════════════════

def get_enriched_context(current_query: str, max_history: int = 10) -> list:
    """
    Deriver L1 히스토리 + Dialectic Recall 통합 컨텍스트.
    hermes_handlers.py의 _cmd_ask 등에서 호출 가능.
    """
    _ensure_initialized()

    # L1 히스토리
    history = _deriver.get_l1_data()
    history = [{"role": m["role"], "content": m["content"]} for m in history[-max_history:]]

    # Dialectic 컨텍스트 요약 회상
    ctx_results = dialectic.recall_relevant_context(current_query, limit=2)

    # Dialectic 의사결정 회상
    dec_results = dialectic.recall_decision(current_query, limit=2)

    recall_parts = []
    if ctx_results:
        recall_parts.append("⚠️ [관련 세션 컨텍스트 오버레이]")
        for ctx in ctx_results:
            recall_parts.append(f"- [{ctx.get('session_id', '?')[:8]}]: {ctx.get('summary', '')[:200]}")

    if dec_results:
        if not recall_parts:
            recall_parts.append("⚠️ [관련 의사결정 오버레이]")
        for dec in dec_results:
            recall_parts.append(f"- 결정: {dec['title'][:80]} → {dec['outcome'][:80]}")

    if recall_parts:
        return [{"role": "system", "content": "\n".join(recall_parts)}] + history

    return history


# ═══════════════════════════════════════════════════════════
# Inline Button Callback 처리
# ═══════════════════════════════════════════════════════════

async def handle_memory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모리 관련 인라인 버튼 콜백 처리."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "mem_search":
        await query.edit_message_text(
            "🔍 **메모리 검색**\n\n"
            "사용법: `/memory_search <검색어>`\n"
            "예: `/memory_search 트레이딩`\n\n"
            "또는 채팅창에 명령어를 직접 입력하세요."
        )
    elif data == "mem_dream":
        await query.edit_message_text("🌙 **Dreaming 시작 중...**")
        try:
            from hybrid_router import router

            async def llm_func(prompt):
                res, name = router.send_completion(prompt)
                return res, name

            result = await _dreamer.dream(history_data=[], llm_func=llm_func)
            await query.edit_message_text(result)
        except Exception as e:
            await query.edit_message_text(f"❌ Dreaming 실패: {e}")
    elif data == "mem_audit":
        # 간단 감사 출력
        ds = _deriver.get_status()
        l2 = _deriver.get_l2_data()
        associations = l2.get("associations", {})
        edge_count = sum(len(targets) for targets in associations.values()) // 2
        await query.edit_message_text(
            "📋 **간단 감사**\n\n"
            f"L1: {ds['l1_count']}/{ds['l1_max']} items\n"
            f"L2: {ds['l2_count']}/{ds['l2_max']} eps (엣지: {edge_count})\n"
            f"Forget candidates: {ds['forget_candidates']}\n"
            f"Semantic Engine: {'ON' if ds['sem_engine'] else 'OFF'}"
        )
    elif data == "mem_save":
        await query.edit_message_text(
            "💾 **중요 기억 저장**\n\n"
            "사용법: `/save <키> <값>`\n"
            "예: `/save 시스템설정 Mac Studio 기본 포트 8080`"
        )
    elif data == "mem_close":
        await query.delete_message()
