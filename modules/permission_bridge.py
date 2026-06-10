"""
PermissionBridge — 2-tier 도구 권한 설정 게이트웨이 (HIGH PRIORITY #1)
====================================================================
SemaClaw 분석 기반 설계: 내부 도구는 자동 승인, 외부 도구는 Telegram 인라인 승인

Tier 정의:
  - INTERNAL (자동 승인): 읽기 전용, 검색, 채팅, 정보 조회
  - EXTERNAL (인라인 승인 필요): bash 실행, 파일 쓰기/삭제, 시스템 변경
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional, Callable, Awaitable

sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts")

# ── Tool Tier Definitions ─────────────────────────────────────────────
# INTERNAL: 자동 승인 (no-op permission)
INTERNAL_TOOLS = {
    "LIST",       # [LIST: /path] — 파일 목록 조회
    "READ",       # [READ: /path] — 파일 내용 읽기
    "SEARCH",     # [SEARCH: query] — 웹 검색
    "WEB_READ",   # [WEB_READ: url] — URL 내용 읽기
}

# EXTERNAL: 인라인 승인 필요
EXTERNAL_TOOLS = {
    "RUN_CMD",    # [RUN_CMD: bash] — bash 명령어 실행
    "DELETE",     # [DELETE: /path] — 파일 삭제
    "MOVE",       # [MOVE: src -> dst] — 파일 이동
    "COPY",       # [COPY: src -> dst] — 파일 복사
    "RENAME",     # [RENAME: old -> new] — 파일명 변경
    "CREATE",     # [CREATE: /path](content)[/CREATE] — 파일 생성
    "SAVE",       # [SAVE: /path]content[/SAVE] — 파일 저장
}

# 승인 대기열
_approval_pending: dict = {}  # {chat_id: {"tool": str, "args": dict, "ts": float, "approved": Optional[bool]}}
_APPROVAL_TIMEOUT = 120  # 초 단위 타임아웃


# ── Classification ────────────────────────────────────────────────────

def classify_tool(tag_name: str) -> str:
    """도구 태그를 Internal / External 로 분류"""
    tag = tag_name.strip().upper()
    if tag in INTERNAL_TOOLS:
        return "internal"
    if tag in EXTERNAL_TOOLS:
        return "external"
    # 미등록 태그는 안전하게 external (보수적 접근)
    return "external"


def needs_approval(tag_name: str) -> bool:
    """이 도구가 승인을 필요로 하는지 확인"""
    return classify_tool(tag_name) == "external"


# ── Approval UI ───────────────────────────────────────────────────────

async def request_approval(
    update,
    context,
    tool_name: str,
    description: str,
    callback_prefix: str = "perm_approve",
    timeout: int = _APPROVAL_TIMEOUT,
) -> bool:
    """
    Telegram 인라인 버튼을 통해 도구 실행 승인을 요청.

    Args:
        update: Telegram Update 객체
        context: Telegram Context 객체
        tool_name: 도구 이름 (예: "RUN_CMD", "DELETE")
        description: 사용자에게 보여줄 설명
        callback_prefix: 콜백 데이터 프리픽스
        timeout: 대기 타임아웃 (초)

    Returns:
        True = 승인됨, False = 거부/타임아웃
    """
    chat_id = update.effective_chat.id

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    approve_data = f"{callback_prefix}:{tool_name}:approve:{chat_id}"
    reject_data = f"{callback_prefix}:{tool_name}:reject:{chat_id}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 승인", callback_data=approve_data),
            InlineKeyboardButton("❌ 거부", callback_data=reject_data),
        ]
    ])

    await update.message.reply_text(
        f"🛡️ **PermissionBridge — 도구 승인 요청**\n\n"
        f"도구: `{tool_name}`\n"
        f"내용: {description}\n\n"
        f"이 작업을 실행하시겠습니까?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    # 승인 대기열 등록
    _approval_pending[chat_id] = {
        "tool": tool_name,
        "args": {"description": description},
        "ts": time.time(),
        "approved": None,  # None = 아직 응답 없음
    }

    # 폴링 방식으로 사용자 응답 대기 (최대 timeout 초)
    start = time.time()
    while time.time() - start < timeout:
        entry = _approval_pending.get(chat_id)
        if entry and entry["approved"] is not None:
            result = entry["approved"]
            del _approval_pending[chat_id]
            return result
        await asyncio.sleep(0.3)

    # 타임아웃
    if chat_id in _approval_pending:
        del _approval_pending[chat_id]
    await update.message.reply_text("⏱️ **PermissionBridge 타임아웃**: 작업이 자동 거부되었습니다.", parse_mode="HTML")
    return False


# ── Callback Handler ──────────────────────────────────────────────────

async def handle_approval_callback(update, context) -> bool:
    """
    인라인 버튼 콜백 처리.
    _base.py의 handle_button_callback()에서 호출됨.

    Returns:
        True = 이 콜백을 PermissionBridge가 처리했음 (다른 핸들러에서 더 이상 처리 금지)
        False = 관련 없는 콜백 (다른 핸들러에 위임)
    """
    query = update.callback_query
    if not query or not query.data:
        return False

    data = query.data
    if not data.startswith("perm_approve:"):
        return False

    await query.answer()

    parts = data.split(":", 4)
    if len(parts) < 4:
        return False

    _, tool_name, decision, chat_id_str = parts[:4]
    chat_id = int(chat_id_str)

    entry = _approval_pending.get(chat_id)
    if not entry:
        await query.edit_message_text("⚠️ 이 승인 요청은 이미 만료되었습니다.")
        return True

    if decision == "approve":
        _approval_pending[chat_id]["approved"] = True
        await query.edit_message_text(
            f"✅ **PermissionBridge — 승인됨**\n\n도구 `{tool_name}` 실행이 승인되었습니다.",
            parse_mode="HTML",
        )
    else:
        _approval_pending[chat_id]["approved"] = False
        await query.edit_message_text(
            f"❌ **PermissionBridge — 거부됨**\n\n도구 `{tool_name}` 실행이 거부되었습니다.",
            parse_mode="HTML",
        )

    return True


# ── Integration Helpers ───────────────────────────────────────────────

def make_external_summary(tag_name: str, tag_args: str) -> str:
    """외부 도구의 실행 요약 설명 생성"""
    summaries = {
        "RUN_CMD": f"bash 명령어 실행: `{tag_args[:100]}`",
        "DELETE": f"파일 삭제: `{tag_args}`",
        "MOVE": f"파일 이동: `{tag_args}`",
        "COPY": f"파일 복사: `{tag_args}`",
        "RENAME": f"파일명 변경: `{tag_args}`",
        "CREATE": f"파일 생성: `{tag_args[:100]}`",
        "SAVE": f"파일 저장: `{tag_args[:100]}`",
    }
    return summaries.get(tag_name, f"외부 작업: `{tag_name}` — `{tag_args[:100]}`")


import asyncio


# ── Interactive Guard ─────────────────────────────────────────────────

async def guard_external_tool(
    update, context, tag_name: str, tag_args: str
) -> tuple[bool, str]:
    """
    외부 도구 실행 전 PermissionBridge 경비.
    INTERNAL 도구는 통과, EXTERNAL 도구는 승인 요청.

    Returns:
        (allowed: bool, reason: str)
    """
    tier = classify_tool(tag_name)

    if tier == "internal":
        return True, "internal"

    # 외부 도구 — 인라인 승인 요청
    summary = make_external_summary(tag_name, tag_args)
    approved = await request_approval(update, context, tag_name, summary)

    if approved:
        return True, "approved"
    else:
        return False, "denied or timeout"


# ── Module-Level Convenience ─────────────────────────────────────────-

def get_pending_entry(chat_id: int) -> Optional[dict]:
    """승인 대기열 항목 조회 (외부에서 사용)"""
    return _approval_pending.get(chat_id)


def clear_pending(chat_id: int):
    """승인 대기열 항목 제거 (타임아웃 등)"""
    _approval_pending.pop(chat_id, None)
