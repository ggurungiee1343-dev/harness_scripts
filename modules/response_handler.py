"""
modules/response_handler.py
===========================
harness_agent.py handle_message() L347~443에서 분리 (2026-06-09).

역할:
  - 진행 애니메이션 (🤔 생각 중...)
  - assemble_context() → get_llm_response() → run_agentic_loop() 파이프라인
  - [SAVE] 태그 처리 (PermissionBridge 승인 포함)
  - 히스토리 저장 + 최종 응답 전송
  - 예외 처리 + 진행 메시지 정리

에러 진단:
  - ImportError: modules.context_assembler_v2 → context_assembler_v2.py 존재 확인
  - ImportError: modules.agentic_loop → agentic_loop.py 존재 확인
  - ImportError: modules.permission_bridge → permission_bridge.py 존재 확인
  - ChatAction 에러 → telegram 패키지 버전 확인
  - get_llm_response 반환값 언패킹 실패 → (ans, engine) 튜플 형식 확인
"""
import os
import re
import asyncio
import unicodedata
from telegram.constants import ChatAction


async def handle_llm_response(
    update,
    context,
    user_text: str,
    *,
    history,
    wiki,
    memory,
    config,
    get_llm_response,
    file_m,
    ctx_builder,
    get_sys_prompt,
) -> None:
    """LLM 호출 → 에이전틱 루프 → 응답 전송 전체 파이프라인."""
    from modules.context_assembler_v2 import assemble_context
    from modules.agentic_loop import run_agentic_loop

    anim_task = None
    progress_msg = None
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )
        progress_msg = await update.message.reply_text("🤔 생각 중...")

        _anim_base_text = ["🤔 생각 중"]

        async def _animate_msg():
            dots = 0
            try:
                while True:
                    await asyncio.sleep(1.5)
                    dots = (dots + 1) % 4
                    try:
                        await context.bot.edit_message_text(
                            f"{_anim_base_text[0]}{'.' * dots}",
                            chat_id=progress_msg.chat_id,
                            message_id=progress_msg.message_id,
                        )
                    except Exception:
                        pass
            except asyncio.CancelledError:
                pass

        anim_task = asyncio.create_task(_animate_msg())

        async def _update_progress(text: str):
            _anim_base_text[0] = text
            try:
                await context.bot.edit_message_text(
                    text,
                    chat_id=progress_msg.chat_id,
                    message_id=progress_msg.message_id,
                )
            except Exception:
                pass

        messages = await assemble_context(
            sys_prompt=get_sys_prompt(),
            user_text=user_text,
            history=history.get_history_for_llm(),
            wiki=wiki,
            memory=memory,
            config=config,
            update_progress_fn=_update_progress,
        )
        ans, engine = await get_llm_response(messages)

        ans = await run_agentic_loop(
            initial_ans=ans,
            messages=messages,
            get_llm_response=get_llm_response,
            update=update,
            context=context,
            file_m=file_m,
            ctx_builder=ctx_builder,
            update_progress_fn=_update_progress,
        )

        # [SAVE] 태그: 절대경로·상대경로 모두 지원 (PermissionBridge 승인 필요)
        save_m = re.search(r"\[SAVE:\s*(.*?)\](.*?)\[/SAVE\]", ans, re.DOTALL)
        if save_m:
            save_path = save_m.group(1).strip()
            try:
                from modules.permission_bridge import guard_external_tool
                allowed, reason = await guard_external_tool(update, context, "SAVE", save_path)
                if not allowed:
                    await progress_msg.delete()
                    await update.message.reply_text(
                        f"🛡️ **PermissionBridge**: SAVE `{save_path}` 차단됨 ({reason})",
                        parse_mode="HTML",
                    )
                    save_m = None
            except Exception:
                pass
        if save_m:
            save_path = save_m.group(1).strip()
            save_content = save_m.group(2).strip()
            if os.path.isabs(save_path):
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(save_content)
            else:
                file_m.write_file(save_path, save_content)

        try:
            anim_task.cancel()
            await progress_msg.delete()
        except Exception:
            pass

        # 규칙 6: 재질문/정정 감지 → SkillLifecycle 피드백 소급 수정
        try:
            from handlers._base import _detect_correction, emit_skill_feedback
            if _detect_correction(user_text):
                # 마지막 스킬 실행 결과를 실패로 소급 기록
                # (현재는 skill_name 추적이 없으므로 "_last_response" sentinel 사용)
                emit_skill_feedback("_last_response", success=False)
        except Exception:
            pass

        history.add_message("user", user_text)
        history.add_message("assistant", ans)

        # 규칙 5: 세션 포화도 경고 — 75%+ 도달 시 응답 말미에 알림 삽입
        try:
            pressure = history.get_context_pressure()
            if pressure["warn"] and not pressure["critical"]:
                pct = int(pressure["ratio"] * 100)
                ans += (
                    f"\n\n---\n💡 **[컨텍스트 {pct}% 소모]** "
                    f"현재 {pressure['turns']}턴 / 압축 기준 {pressure['turns']*100//pct}턴. "
                    "작업이 길어질 경우 새 창에서 이어가는 것을 권장합니다."
                )
            elif pressure["critical"]:
                ans += (
                    "\n\n---\n⚠️ **[컨텍스트 포화]** "
                    "대화 히스토리가 자동 압축되었습니다. "
                    "중요한 작업은 새 창에서 시작하세요."
                )
        except Exception:
            pass

        from handlers._base import _reply_long
        _self_id_keywords = ("너는 누구", "무슨 api", "어떤 api", "무슨 모델", "어떤 모델", "뭔 api", "뭔 모델", "너 api", "너 모델")
        _ask_lower = user_text.lower()
        if any(k in _ask_lower for k in _self_id_keywords):
            final_ans = unicodedata.normalize("NFC", ans) + f"\n\n현재 선택된 엔진: `{engine}`"
        else:
            final_ans = unicodedata.normalize("NFC", ans)
        await _reply_long(update.message, final_ans)

    except Exception as e:
        try:
            if anim_task:
                anim_task.cancel()
            if progress_msg:
                await progress_msg.delete()
        except Exception:
            pass
        from handlers._base import _reply_long
        await _reply_long(update.message, f"⚠️ 에러: {e}")
