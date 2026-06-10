"""
Optimistic Response Engine v1.0
Linear 아키텍처 기반: 사용자에게 즉시 응답 → 백그라운드 처리

패턴:
  1. 즉시 "진행 중" 메시지 발송
  2. 백그라운드에서 실제 작업
  3. 성공/실패 결과로 메시지 편집
  4. 3회 실패 시 수동 개입 안내
"""

import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional, Dict, Any

logger = logging.getLogger("HermesOrchestrator")

# ── 상태 상수 ────────────────────────────────────────────────────
STATUS_IN_PROGRESS = "in_progress"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

MAX_RETRIES = 3
RETRY_DELAY = 2  # 초


class OptimisticAction:
    """진행 중인 작업 트래킹"""

    def __init__(self, action_id: str, action_name: str, user_id: int):
        self.action_id = action_id
        self.action_name = action_name
        self.user_id = user_id
        self.status = STATUS_IN_PROGRESS
        self.message_id: int = -1
        self.chat_id: int = -1
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.result: Optional[Dict[str, Any]] = None
        self.error_msg: Optional[str] = None
        self.retry_count: int = 0

    def duration_str(self) -> str:
        secs = (datetime.now() - self.created_at).total_seconds()
        return f"{secs:.1f}"


class OptimisticResponseEngine:
    """낙관적 응답 관리기 (모듈 싱글턴으로 사용)"""

    def __init__(self):
        self._actions: Dict[str, OptimisticAction] = {}
        self._counter = 0

    def _new_id(self, name: str) -> str:
        self._counter += 1
        ts = int(datetime.now().timestamp() * 1000)
        return f"{name}_{self._counter}_{ts}"

    async def initiate_action(
        self,
        bot,
        chat_id: int,
        user_id: int,
        action_name: str,
        description: str,
        work_fn: Callable,
        on_complete: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ) -> OptimisticAction:
        """
        작업 시작 — 즉시 피드백 메시지 발송 후 백그라운드 실행.

        Args:
            bot: telegram Bot 인스턴스 (context.bot)
            work_fn: 실제 작업 (async/sync 모두 지원), dict 반환 권장
            on_complete: 성공 콜백 (result dict 전달)
            on_error: 실패 콜백 (error_msg, recovery_hint 전달)
        """
        action_id = self._new_id(action_name)
        action = OptimisticAction(action_id, action_name, user_id)
        action.chat_id = chat_id
        self._actions[action_id] = action

        # 즉시 피드백
        short_id = action_id[:20] + "..."
        progress_text = f"⏳ {description}\n\n<code>{short_id}</code>"
        try:
            msg = await bot.send_message(chat_id=chat_id, text=progress_text, parse_mode="HTML")
            action.message_id = msg.message_id
        except Exception as e:
            logger.error(f"[OPT] 즉시 피드백 발송 실패: {e}")

        # 백그라운드 실행
        asyncio.create_task(
            self._run(bot, action, work_fn, on_complete, on_error)
        )
        return action

    async def _run(
        self,
        bot,
        action: OptimisticAction,
        work_fn: Callable,
        on_complete: Optional[Callable],
        on_error: Optional[Callable],
    ) -> None:
        for attempt in range(MAX_RETRIES):
            try:
                if asyncio.iscoroutinefunction(work_fn):
                    result = await work_fn()
                else:
                    result = work_fn()

                # 성공
                action.status = STATUS_SUCCESS
                action.result = result
                action.updated_at = datetime.now()

                text = self._success_text(action)
                await self._edit_or_send(bot, action, text)

                if on_complete:
                    if asyncio.iscoroutinefunction(on_complete):
                        await on_complete(result)
                    else:
                        on_complete(result)

                logger.info(f"[OPT] ✅ {action.action_name} 완료 ({action.duration_str()}s)")
                return

            except Exception as e:
                action.error_msg = str(e)
                action.retry_count = attempt + 1
                action.updated_at = datetime.now()
                logger.warning(f"[OPT] {action.action_name} 시도 {attempt+1}/{MAX_RETRIES} 실패: {e}")

                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)
                    continue

                # 최종 실패
                action.status = STATUS_FAILED
                text = self._error_text(action)
                await self._edit_or_send(bot, action, text)

                if on_error:
                    hint = _recovery_hint(action.error_msg or "")
                    if asyncio.iscoroutinefunction(on_error):
                        await on_error(error_msg=action.error_msg, recovery_hint=hint)
                    else:
                        on_error(error_msg=action.error_msg, recovery_hint=hint)

                logger.error(f"[OPT] ❌ {action.action_name} 최종 실패: {action.error_msg}")

    def _success_text(self, action: OptimisticAction) -> str:
        extras = ""
        r = action.result or {}
        if "file_path" in r:
            extras += f"\n📄 경로: <code>{r['file_path']}</code>"
        if "summary" in r:
            s = str(r["summary"])
            extras += f"\n📝 {s[:120]}{'...' if len(s) > 120 else ''}"
        if "processed" in r:
            extras += f"\n📊 처리: {r['processed']}개"
        return (
            f"✅ <b>{action.action_name} 완료</b> ({action.duration_str()}초)"
            f"{extras}"
        )

    def _error_text(self, action: OptimisticAction) -> str:
        err = (action.error_msg or "알 수 없는 오류")[:120]
        return (
            f"❌ <b>{action.action_name} 실패</b> ({action.retry_count}/{MAX_RETRIES}회 시도)\n\n"
            f"오류: <code>{err}</code>\n\n"
            f"💡 다음 단계:\n"
            f"• /retry — 마지막 작업 재시도\n"
            f"• /status — 시스템 상태 확인\n"
            f"• /audit — 상세 로그 조회"
        )

    async def _edit_or_send(self, bot, action: OptimisticAction, text: str) -> None:
        try:
            if action.message_id > 0:
                await bot.edit_message_text(
                    chat_id=action.chat_id,
                    message_id=action.message_id,
                    text=text,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(chat_id=action.chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[OPT] 메시지 편집 실패: {e}")

    def last_failed(self, user_id: int) -> Optional[OptimisticAction]:
        """사용자의 가장 최근 실패 작업 반환"""
        candidates = [
            a for a in self._actions.values()
            if a.user_id == user_id and a.status == STATUS_FAILED
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.updated_at)

    def cleanup(self, max_age_seconds: int = 3600) -> int:
        """오래된 완료/실패 작업 정리, 제거된 수 반환"""
        now = datetime.now()
        expired = [
            k for k, a in self._actions.items()
            if a.status != STATUS_IN_PROGRESS
            and (now - a.updated_at).total_seconds() > max_age_seconds
        ]
        for k in expired:
            del self._actions[k]
        return len(expired)


def _recovery_hint(error_msg: str) -> Dict[str, str]:
    err = error_msg.lower()
    if "permission" in err or "denied" in err:
        return {"category": "권한", "hint": "경로 권한 확인", "command": "/audit check-permissions"}
    if "network" in err or "timeout" in err or "connection" in err:
        return {"category": "네트워크", "hint": "API 연결 실패 — 모델 전환 후 재시도", "command": "/model nvidia"}
    if "memory" in err or "out of memory" in err:
        return {"category": "메모리", "hint": "시스템 메모리 부족", "command": "/status"}
    if "file" in err or "not found" in err or "no such" in err:
        return {"category": "파일", "hint": "경로 재확인 필요", "command": "/vault check"}
    return {"category": "알 수 없음", "hint": "상세 로그 확인", "command": "/audit"}


# ── 모듈 싱글턴 ──────────────────────────────────────────────────
_engine: Optional[OptimisticResponseEngine] = None


def get_engine() -> OptimisticResponseEngine:
    global _engine
    if _engine is None:
        _engine = OptimisticResponseEngine()
    return _engine
