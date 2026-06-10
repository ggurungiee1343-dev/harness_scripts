"""
파일 작업 레이어 — harness_agent.py에서 분리 (2026-06-09)
NL 파일 작업 확인 대기열, 보안 경로 검사, 파일 작업 실행 포함
"""

import os
import shutil
import pathlib

# ── 파일 작업 확인 대기열 ─────────────────────────────────
_file_op_pending: dict = {}  # {chat_id: {"action": str, "path": str, "dest": str|None}}

# ── 보안 경로 (허용된 작업 영역) ──────────────────────────
ALLOWED_BASE_PATHS = [
    "/Users/bluesea/Applications",
    "/Users/bluesea/hermes",
    "/Users/bluesea/.hermes",
]


def is_path_allowed(path: str) -> bool:
    resolved = str(pathlib.Path(path).resolve())
    return any(
        resolved.startswith(base.rstrip("/") + "/") or resolved == base.rstrip("/")
        for base in ALLOWED_BASE_PATHS
    )


def safe_remove(path: str) -> str:
    """파일을 영구 삭제 대신 Trash로 이동 (복구 가능)"""
    import subprocess
    result = subprocess.run(
        ["osascript", "-e", f'tell application "Finder" to delete POSIX file "{path}"'],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        trash_dir = os.path.expanduser("~/.Trash")
        basename = os.path.basename(path)
        dest = os.path.join(trash_dir, basename)
        counter = 1
        while os.path.exists(dest):
            name, ext = os.path.splitext(basename)
            dest = os.path.join(trash_dir, f"{name}_{counter}{ext}")
            counter += 1
        shutil.move(path, dest)
        return f"🗑️ Trash로 이동됨: {dest}"
    return "🗑️ Finder를 통해 Trash로 이동됨"


async def handle_file_op(update, context, action, path, dest=None):
    """파일 작업 실행 (삭제/이동/복사/이름변경/생성)"""
    try:
        from modules.permission_bridge import guard_external_tool
        tag_map = {"DELETE": "DELETE", "MOVE": "MOVE", "COPY": "COPY",
                   "RENAME": "RENAME", "CREATE": "CREATE"}
        desc = f"{action}: {path}" + (f" → {dest}" if dest else "")
        allowed, reason = await guard_external_tool(update, context, tag_map.get(action, "FILE_OP"), desc)
        if not allowed:
            await update.message.reply_text(
                f"🛡️ **PermissionBridge**: `{action}` 차단됨 ({reason})", parse_mode="HTML")
            return
    except (ImportError, Exception):
        pass

    try:
        abs_path = str(pathlib.Path(path).expanduser().resolve())
        if not is_path_allowed(abs_path):
            await update.message.reply_text(f"⛔ 허용되지 않은 경로입니다: {abs_path}")
            return

        if action == "DELETE":
            if not os.path.exists(abs_path):
                await update.message.reply_text(f"⚠️ 파일이 존재하지 않습니다: {abs_path}")
                return
            _file_op_pending[update.effective_chat.id] = {"action": "DELETE", "path": abs_path}
            await update.message.reply_text(
                f"⚠️ **삭제 확인**\n\n"
                f"파일: `{abs_path}`\n\n"
                f"정말 삭제하시겠습니까?\n"
                f"→ `/confirm` 로 승인\n"
                f"→ `/cancel` 로 취소",
                parse_mode="HTML"
            )

        elif action == "MOVE":
            if not os.path.exists(abs_path):
                await update.message.reply_text(f"⚠️ 파일이 존재하지 않습니다: {abs_path}")
                return
            abs_dest = str(pathlib.Path(dest).expanduser().resolve())
            if not is_path_allowed(abs_dest):
                await update.message.reply_text(f"⛔ 허용되지 않은 목적지 경로입니다: {abs_dest}")
                return
            _file_op_pending[update.effective_chat.id] = {"action": "MOVE", "path": abs_path, "dest": abs_dest}
            await update.message.reply_text(
                f"⚠️ **이동 확인**\n\n"
                f"대상: `{abs_path}`\n"
                f"목적지: `{abs_dest}`\n\n"
                f"→ `/confirm` 로 승인\n"
                f"→ `/cancel` 로 취소",
                parse_mode="HTML"
            )

        elif action == "COPY":
            abs_dest = str(pathlib.Path(dest).expanduser().resolve())
            if not is_path_allowed(abs_dest):
                await update.message.reply_text(f"⛔ 허용되지 않은 목적지 경로입니다: {abs_dest}")
                return
            shutil.copy2(abs_path, abs_dest)
            await update.message.reply_text(f"✅ 복사 완료: `{abs_path}` → `{abs_dest}`", parse_mode="HTML")

        elif action == "RENAME":
            abs_dest = str(pathlib.Path(dest).expanduser().resolve())
            if os.path.exists(abs_dest):
                await update.message.reply_text(f"⚠️ 이미 존재하는 파일명입니다: {abs_dest}")
                return
            os.rename(abs_path, abs_dest)
            await update.message.reply_text(f"✅ 이름변경 완료: `{abs_path}` → `{abs_dest}`", parse_mode="HTML")

        elif action == "CREATE":
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(dest or "")
            await update.message.reply_text(f"✅ 파일 생성 완료: `{abs_path}`", parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"⚠️ 파일 작업 실패: {e}")
