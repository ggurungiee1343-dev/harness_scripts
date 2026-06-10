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
  - ImportError: modules.context_assembler → context_assembler.py 존재 확인
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
    from modules.context_assembler import assemble_context
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
            history=history,
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

        history.add_message("user", user_text)
        history.add_message("assistant", ans)
        from handlers._base import _reply_long
        await _reply_long(
            update.message,
            f"🧠 [{engine}]\n\n" + unicodedata.normalize("NFC", ans),
        )

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
