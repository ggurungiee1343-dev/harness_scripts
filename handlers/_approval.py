# handlers/_approval.py
# Hermes3 Phase 2 - /tag pending | approve | reject 핸들러
from modules.tag_linker import TagLinker


class ApprovalHandler:
    def __init__(self):
        self.tag_linker = TagLinker()

    async def handle_tag_command(self, update, context):
        """
        사용 예:
        /tag pending
        /tag approve 3
        /tag reject 5
        """
        text = (update.message.text or "").strip()
        parts = text.split()

        if len(parts) < 2:
            return await update.message.reply_text(
                "사용법: /tag pending | /tag approve <id> | /tag reject <id>"
            )

        action = parts[1].lower()

        if action == 'pending':
            pending = self.tag_linker.list_pending()
            if not pending:
                return await update.message.reply_text("대기 중인 태그 제안이 없습니다.")

            lines = ["📌 Pending tag approvals"]
            for item in pending[:20]:
                lines.append(
                    f"#{item['id']} | {item['tag']} | {item['confidence']:.2f} | {item['file_path']}"
                )
            return await update.message.reply_text("\n".join(lines))

        if action in ('approve', 'reject'):
            if len(parts) < 3 or not parts[2].isdigit():
                return await update.message.reply_text(
                    f"사용법: /tag {action} <id>"
                )

            approval_id = int(parts[2])
            if action == 'approve':
                ok = self.tag_linker.approve_tag(approval_id)
                if ok:
                    return await update.message.reply_text(f"✅ approval #{approval_id} 승인 완료")
                return await update.message.reply_text(f"❌ approval #{approval_id}를 찾을 수 없거나 이미 처리됨")

            if action == 'reject':
                ok = self.tag_linker.reject_tag(approval_id)
                if ok:
                    return await update.message.reply_text(f"🗑️ approval #{approval_id} 거절 완료")
                return await update.message.reply_text(f"❌ approval #{approval_id}를 찾을 수 없거나 이미 처리됨")

        return await update.message.reply_text(
            "알 수 없는 명령입니다. 사용법: /tag pending | /tag approve <id> | /tag reject <id>"
        )


async def cmd_tag(update, context):
    handler = ApprovalHandler()
    await handler.handle_tag_command(update, context)


async def cmd_tag_logic(query: str) -> str:
    """Core reducer에서 호출 가능한 태그 처리 핵심 로직.

    보안: 한글/특수문자가 포함된 쿼리가 셸로 전달되지 않도록
    action 키워드 검증을 엄격하게 수행한다.
    """
    import shlex

    # 1단계: shlex.quote()로 쿼리 escape 처리 (셸 주입 방지)
    safe_query = shlex.quote(query.strip())

    parts = query.strip().split()
    if not parts:
        return "사용법: pending | approve <id> | reject <id>"

    action = parts[0].lower()

    # 2단계: 알려진 액션만 허용 — 나머지는 태그 명령어가 아님
    VALID_ACTIONS = ("pending", "approve", "reject")
    if action not in VALID_ACTIONS:
        return (
            f"⚠️ 태그 처리가 아닌 일반 질의로 판단됩니다: '{action}...'\n"
            f"사용법: /tag pending | /tag approve <id> | /tag reject <id>"
        )

    linker = TagLinker()

    if action == 'pending':
        pending = linker.list_pending()
        if not pending:
            return "대기 중인 태그 제안이 없습니다."

        lines = ["📌 Pending tag approvals"]
        for item in pending[:20]:
            lines.append(
                f"#{item['id']} | {item['tag']} | {item['confidence']:.2f} | {item['file_path']}"
            )
        return "\n".join(lines)

    # approve / reject
    if len(parts) < 2 or not parts[1].isdigit():
        return f"사용법: {action} <id>"

    approval_id = int(parts[1])
    if action == 'approve':
        ok = linker.approve_tag(approval_id)
        if ok:
            return f"✅ approval #{approval_id} 승인 완료"
        return f"❌ approval #{approval_id}를 찾을 수 없거나 이미 처리됨"

    if action == 'reject':
        ok = linker.reject_tag(approval_id)
        if ok:
            return f"🗑️ approval #{approval_id} 거절 완료"
        return f"❌ approval #{approval_id}를 찾을 수 없거나 이미 처리됨"

    return f"알 수 없는 태그 명령입니다: {action}"
