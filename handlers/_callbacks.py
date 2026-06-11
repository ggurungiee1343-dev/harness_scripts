"""handlers._callbacks — InlineKeyboard 버튼 콜백 처리 (2026-06-09 _base.py에서 분리)"""
import os
import logging
import signal
import subprocess
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from modules.kanban_manager import KanbanDB
from modules.optimistic_response import get_engine, STATUS_FAILED

logger = logging.getLogger("HermesOrchestrator")

# _get_mem_info는 _base.py에서 lazy import (순환 방지)
async def _get_mem_info():
    from handlers._base import _get_mem_info as _fn, safe_reply, safe_edit
    return await _fn()

async def handle_retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/retry — 마지막 실패 작업 재시도 안내"""
    user_id = update.effective_user.id
    opt = get_engine()
    action = opt.last_failed(user_id)

    if not action:
        await safe_reply(update.message, "⚠️ 재시도할 실패 작업이 없습니다.")
        return

    await safe_reply(update.message, 
        f"🔄 <b>마지막 실패 작업 정보</b>\n\n"
        f"작업: <code>{action.action_name}</code>\n"
        f"오류: <code>{(action.error_msg or '')[:100]}</code>\n\n"
        f"해당 명령어를 다시 실행하세요.",
        parse_mode="HTML",
    )


async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """인라인 승인 버튼 클릭 처리"""
    query = update.callback_query
    await query.answer()

    data = query.data

    # === Verify Harness 콜백 처리 ===
    if data == 'verify_harness_run':
        try:
            from handlers._ui import callback_verify_harness_run
            await callback_verify_harness_run(update, context)
        except Exception as e:
            logger.error(f'verify_harness_run 오류: {e}')
            await query.edit_message_text(f'❌ 진단 실패: {e}')
        return

    if data == 'verify_harness_cancel':
        try:
            from handlers._ui import callback_verify_harness_cancel
            await callback_verify_harness_cancel(update, context)
        except Exception as e:
            logger.error(f'verify_harness_cancel 오류: {e}')
            await query.edit_message_text(f'❌ 취소 처리 실패: {e}')
        return

    # === Harness 콜백 위임 ===
    if data.startswith('harness_'):
        try:
            from hermes_harness import _handle_harness_callback
            await _handle_harness_callback(update, context)
        except Exception as e:
            await query.edit_message_text(f'❌ Harness 콜백 오류: {e}')
        return

    # === 메모리 캐시 정리 (sudo purge) ===
    if data == 'mem_purge_confirm':
        await query.edit_message_text('🧹 메모리 캐시 정리 중... 잠시만 기다려 주세요.')
        try:
            before = await _get_mem_info()
            purge_proc = await asyncio.create_subprocess_shell(
                'sync && sudo /usr/sbin/purge',
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            await purge_proc.communicate()
            await asyncio.sleep(1)
            after = await _get_mem_info()
            recovered = before['cached_gb'] - after['cached_gb']
            result = (
                f'✅ **메모리 캐시 정리 완료!**\n\n'
                f'▸ 정리 전 캐시: `{before["cached_gb"]:.1f} GB`\n'
                f'▸ 정리 후 캐시: `{after["cached_gb"]:.1f} GB`\n'
                f'▸ **확보된 램: `{recovered:.1f} GB`** 🎉\n\n'
                f'현재 진짜 여유 공간: `{after["free_gb"]:.1f} GB`'
            )
            await query.edit_message_text(result, parse_mode='HTML')
        except Exception as e:
            logger.error(f'Purge error: {e}')
            await query.edit_message_text(f'❌ 캐시 정리 중 오류: {e}')
        return

    # === Kanban 콜백 처리 ===
    db = KanbanDB()
    if data.startswith('kanban_move_'):
        parts = data.split('_')
        if len(parts) >= 4:
            card_id = int(parts[2])
            target_col = parts[3]
            try:
                success = db.move_card(card_id, target_col)
                if success:
                    await query.edit_message_text(f'✅ 카드 `{card_id}` 를 **{target_col}** 로 이동했습니다.', parse_mode='HTML')
                else:
                    await query.edit_message_text(f'❌ 카드 `{card_id}` 를 찾을 수 없거나 이동에 실패했습니다.', parse_mode='HTML')
            except Exception as e:
                await query.edit_message_text(f'❌ 이동 중 오류: {e}', parse_mode='HTML')
        return

    if data.startswith('kanban_del_'):
        parts = data.split('_')
        if len(parts) >= 3:
            card_id = int(parts[2])
            try:
                success = db.delete_card(card_id)
                if success:
                    await query.edit_message_text(f'🗑️ 카드 `{card_id}` 를 삭제했습니다.', parse_mode='HTML')
                else:
                    await query.edit_message_text(f'❌ 카드 `{card_id}` 를 찾을 수 없습니다.', parse_mode='HTML')
            except Exception as e:
                await query.edit_message_text(f'❌ 삭제 중 오류: {e}', parse_mode='HTML')
        return

    if data.startswith('kanban_reclaim_'):
        parts = data.split('_')
        if len(parts) >= 3:
            card_id = int(parts[2])
            try:
                success = db.move_card(card_id, 'TODO')
                if success:
                    await query.edit_message_text(f'🔄 카드 `{card_id}` 회수 → TODO (좀비 워커 해제)', parse_mode='HTML')
                else:
                    await query.edit_message_text(f'❌ 카드 `{card_id}` 회수 실패', parse_mode='HTML')
            except Exception as e:
                await query.edit_message_text(f'❌ 회수 중 오류: {e}', parse_mode='HTML')
        return

    # === PermissionBridge 콜백 위임 ===
    if data.startswith('perm_approve:'):
        try:
            from modules.permission_bridge import handle_approval_callback
            handled = await handle_approval_callback(update, context)
            if handled:
                return
        except Exception as e:
            logger.error(f'PermissionBridge 콜백 오류: {e}')
            await query.edit_message_text(f'⚠️ PermissionBridge 콜백 오류: {e}')
        return

    # === 메모리 콜백 위임 ===
    if data.startswith('mem_'):
        try:
            from hermes_memory_patch import handle_memory_callback
            await handle_memory_callback(update, context)
        except Exception as e:
            logger.error(f'메모리 콜백 처리 실패: {e}')
            await query.edit_message_text(f'❌ 메모리 콜백 오류: {e}')
        return

    # === 프로세스 종료 (kill) ===
    if data.startswith('kill_proc_'):
        pid = data.replace('kill_proc_', '')
        try:
            pid_int = int(pid)
            res = None
            try:
                os.kill(pid_int, signal.SIGTERM)
                await asyncio.sleep(2)
                try:
                    os.kill(pid_int, 0)
                    os.kill(pid_int, signal.SIGKILL)
                    res = f'💥 `{pid}` — SIGKILL 로 강제 종료했습니다'
                except OSError:
                    res = f'✅ `{pid}` — 정상 종료 완료'
            except OSError:
                res = f'✅ `{pid}` — 정상 종료 완료'
            text = query.message.text or ''
            await query.edit_message_text(
                text + '\n\n======================\n' + res,
                parse_mode='HTML'
            )
        except Exception as e:
            text = query.message.text or ''
            await query.edit_message_text(
                text + '\n\n======================\n❌ 종료 실패: ' + str(e),
                parse_mode='HTML'
            )
        return

    # === Dreaming 승인 ===
    if data == 'confirm_dreaming':
        await query.edit_message_text(
            query.message.text + '\n\n✅ Dreaming 실행 승인됨',
            parse_mode='HTML'
        )
        from handlers._memory import cmd_dreaming
        await cmd_dreaming(update, context)
        return

    # === Ingest 승인 ===
    if data == 'confirm_ingest':
        await query.edit_message_text(
            query.message.text + '\n\n✅ Ingest 실행 승인됨',
            parse_mode='HTML'
        )
        from handlers._file import cmd_ingest
        await cmd_ingest(update, context)
        return

    # === 모드 전환 콜백 ===
    if data.startswith('mode_sel_'):
        from harness_agent import set_mode, get_current_mode, ALL_MODES, MODE_LABELS, MODE_FILE
        mode = data.replace('mode_sel_', '')
        set_mode(mode)
        try:
            await query.edit_message_text(
                f"✅ **모드 전환 완료**\n\n"
                f"🟢 **{MODE_LABELS.get(mode, mode)}**(으)로 변경되었습니다.\n"
                f"선택한 엔진이 실패하면 자동 폴백됩니다.",
                parse_mode="HTML"
            )
        except Exception:
            await update.effective_chat.send_message(
                f"✅ **{MODE_LABELS.get(mode, mode)}**(으)로 모드 전환 완료!",
                parse_mode="HTML"
            )
        return

    # === PENDING_TASKS 처리 (파일 작업 승인) ===
    action_info = PENDING_TASKS.get(data)
    if action_info:
        action_type = action_info.get('action', '')
        args = action_info.get('args', {})
        res_text = ''
        try:
            if action_type == 'delete_file':
                target_path = args.get('path', '')
                if not secure_path(target_path):
                    res_text = f'🔒 [보안 경고] 허용되지 않은 경로: {target_path}'
                elif not os.path.exists(target_path):
                    res_text = f'❌ [오류] 파일을 찾을 수 없습니다: {target_path}'
                else:
                    os.remove(target_path)
                    res_text = f'🗑️ [삭제 완료] {target_path}'
            elif action_type == 'move_file':
                src = args.get('source_path', '')
                dest_dir = args.get('target_dir', '')
                if not secure_path(src) or not secure_path(dest_dir):
                    res_text = f'🔒 [보안 경고] 허용되지 않은 경로 조합'
                elif not os.path.exists(src):
                    res_text = f'❌ [오류] 원본 파일을 찾을 수 없습니다: {src}'
                else:
                    os.makedirs(dest_dir, exist_ok=True)
                    dest_path = os.path.join(dest_dir, Path(src).name)
                    shutil.move(src, dest_path)
                    res_text = f'🚚 [이동 완료] {src} ➔ {dest_path}'
            else:
                res_text = f'❌ [오류] 알 수 없는 작업: {action_type}'
        except (OSError, PermissionError) as e:
            res_text = f'❌ [실행 실패] {e}'
        PENDING_TASKS.pop(data, None)
        original_text = query.message.text or query.message.caption or ''
        await query.edit_message_text(
            original_text + '\n\n' + res_text,
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text(
            (query.message.text or '') + '\n\n⚠️ 만료되었거나 이미 처리된 작업입니다.',
            parse_mode='HTML'
        )


