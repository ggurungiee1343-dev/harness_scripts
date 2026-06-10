"""handlers._kanban — 칸반 명령어 (v2: heartbeat + zombie detection + multi-worker)"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers._base import (router, cove_engine_instance, _audit_engine,
    logger, add_to_history, _call_llm, _get_mem_info, check_user)
from modules.kanban_manager import KanbanDB


async def cmd_kanban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kanban command - Kanban board with heartbeat & multi-worker."""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ 사용법: `/kanban [list|add|move|delete|claim|heartbeat|release|workers] ...`\n"
            "`list` - 현재 보드 표시 (좀비/워커 정보 포함)\n"
            "`add <title>` - 새 카드 추가 (TODO)\n"
            "`move <id> <column>` - 카드 이동 (TODO/IN_PROGRESS/DONE)\n"
            "`claim <id> <worker>` - 워커가 카드 할당\n"
            "`heartbeat <id>` - 작업 중 신호 전송\n"
            "`release <id> [success|fail]` - 작업 완료/포기\n"
            "`delete <id>` - 카드 삭제\n"
            "`workers` - 워커 상태 및 좀비 현황",
            parse_mode='Markdown'
        )
        return

    subcmd = context.args[0].lower()
    db = KanbanDB()

    if subcmd == "list":
        board = db.list_cards()
        text = "*🗂️ Kanban 보드*\\n\\n"
        keyboard = []
        for col in ("TODO", "IN_PROGRESS", "DONE"):
            text += f"*{col}*\\n"
            for card in board[col]:
                card_id = card['id']
                title = card['title']
                worker = card.get('worker_id')
                zombie = card.get('zombie', False)
                retry = card.get('retry_budget', 3)

                worker_tag = f" [{worker}]" if worker else ""
                zombie_tag = " 💀" if zombie else ""
                retry_tag = f" (재시도 {retry}/3)" if col == 'IN_PROGRESS' else ""
                text += f"`{card_id}`: {title}{worker_tag}{zombie_tag}{retry_tag}\\n"

                move_buttons = []
                for target in ("TODO", "IN_PROGRESS", "DONE"):
                    if target != col:
                        move_buttons.append(InlineKeyboardButton(f"→{target}", callback_data=f"kanban_move_{card_id}_{target}"))
                delete_button = InlineKeyboardButton("🗑", callback_data=f"kanban_del_{card_id}")
                if zombie:
                    reclaim_button = InlineKeyboardButton("🔄 Reclaim", callback_data=f"kanban_reclaim_{card_id}")
                    row = move_buttons + [reclaim_button, delete_button]
                else:
                    row = move_buttons + [delete_button]
                keyboard.append(row)

            if not board[col]:
                text += "_없음_\\n"
            text += "\\n"

        # 통계줄
        total = sum(len(board[c]) for c in board)
        zombies = sum(1 for c in board['IN_PROGRESS'] if c.get('zombie'))
        if zombies:
            text += f"⚠️ *좀비 카드: {zombies}개* (5분 이상 응답 없음)\\n"
        text += f"📊 총 {total}개 카드"

        if not keyboard:
            await update.message.reply_text(text, parse_mode='Markdown')
        else:
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return

    if subcmd == "add":
        title = " ".join(context.args[1:]).strip()
        if not title:
            await update.message.reply_text("⚠️ 카드 제목을 입력해 주세요.", parse_mode='Markdown')
            return
        card_id = db.add_card(title)
        await update.message.reply_text(f"✅ 카드 추가됨 (`{card_id}`): {title}", parse_mode='Markdown')
        return

    if subcmd == "move":
        if len(context.args) < 3:
            await update.message.reply_text("⚠️ 사용법: `/kanban move <id> <column>`", parse_mode='Markdown')
            return
        try:
            card_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ 카드 ID는 숫자여야 합니다.", parse_mode='Markdown')
            return
        column = context.args[2].upper()
        if column not in ("TODO", "IN_PROGRESS", "DONE"):
            await update.message.reply_text("⚠️ column은 TODO, IN_PROGRESS, DONE 중 하나여야 합니다.", parse_mode='Markdown')
            return
        success = db.move_card(card_id, column)
        if success:
            await update.message.reply_text(f"✅ 카드 `{card_id}` 를 **{column}** 로 이동했습니다.", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ 카드 `{card_id}` 를 찾을 수 없거나 이동에 실패했습니다.", parse_mode='Markdown')
        return

    if subcmd == "claim":
        if len(context.args) < 3:
            await update.message.reply_text("⚠️ 사용법: `/kanban claim <id> <worker>`", parse_mode='Markdown')
            return
        try:
            card_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ 카드 ID는 숫자여야 합니다.", parse_mode='Markdown')
            return
        worker_id = context.args[2].strip()
        success = db.claim_card(card_id, worker_id)
        if success:
            await update.message.reply_text(f"✅ `{worker_id}` 가 카드 `{card_id}` 할당받음 (retry=3)", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ 카드 `{card_id}` 를 찾을 수 없거나 이미 할당됨.", parse_mode='Markdown')
        return

    if subcmd == "heartbeat":
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ 사용법: `/kanban heartbeat <id>`", parse_mode='Markdown')
            return
        try:
            card_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ 카드 ID는 숫자여야 합니다.", parse_mode='Markdown')
            return
        # sender_id를 워커 ID로 사용
        worker_id = str(update.effective_user.id)
        success = db.heartbeat(card_id, worker_id)
        if success:
            await update.message.reply_text(f"💓 카드 `{card_id}` heartbeat 갱신됨", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ 카드 `{card_id}` 를 찾을 수 없거나 워커 불일치", parse_mode='Markdown')
        return

    if subcmd == "release":
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ 사용법: `/kanban release <id> [success|fail]`", parse_mode='Markdown')
            return
        try:
            card_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ 카드 ID는 숫자여야 합니다.", parse_mode='Markdown')
            return
        success = len(context.args) < 3 or context.args[2].lower() != "fail"
        worker_id = str(update.effective_user.id)
        ok = db.release_card(card_id, worker_id, success=success)
        if ok:
            status = "완료(DONE)" if success else "실패(retry 차감)"
            await update.message.reply_text(f"✅ 카드 `{card_id}` {status}", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ 카드 `{card_id}` 해제 실패 (미할당/워커 불일치)", parse_mode='Markdown')
        return

    if subcmd == "workers":
        stats = db.get_worker_stats()
        zombies = db.get_zombies()
        text = "*👷 워커 현황*\\n\\n"

        if stats['active']:
            text += "*🟢 활성 워커*\\n"
            for wid, cnt in stats['active'].items():
                text += f"  `{wid}`: {cnt}개 작업 중\\n"
        else:
            text += "활성 워커 없음\\n"

        if stats['done']:
            text += "\\n*✅ 완료 실적*\\n"
            for wid, cnt in stats['done'].items():
                text += f"  `{wid}`: {cnt}개 완료\\n"

        if zombies:
            text += f"\\n*💀 좀비 카드 ({len(zombies)}개)*\\n"
            for z in zombies:
                text += f"  `{z['id']}` {z['title'][:30]} — 워커: {z['worker_id']} (예산: {z['retry_budget']}/3)\\n"
            text += "\\n👉 `/kanban reclaim <id>` 로 회수하거나 `/kanban move <id> TODO` 로 되돌리세요."

        await update.message.reply_text(text, parse_mode='Markdown')
        return

    if subcmd == "reclaim":
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ 사용법: `/kanban reclaim <id>`", parse_mode='Markdown')
            return
        try:
            card_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ 카드 ID는 숫자여야 합니다.", parse_mode='Markdown')
            return
        # 좀비 카드 회수 → TODO로 이동 + worker 해제
        success = db.move_card(card_id, 'TODO')
        if success:
            await update.message.reply_text(f"🔄 카드 `{card_id}` 회수 → TODO (워커 해제됨)", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ 카드 `{card_id}` 회수 실패", parse_mode='Markdown')
        return

    await update.message.reply_text("⚠️ 알 수 없는 subcommand. 사용법: `/kanban [list|add|move|delete|claim|heartbeat|release|workers]`", parse_mode='Markdown')
