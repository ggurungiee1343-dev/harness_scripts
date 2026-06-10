"""
hermes_file_ops.py — 파일 안전 조작 핸들러 (확인 시스템 포함)
===============================================================
/create, /read, /move, /copy, /delete, /rename, /list
/confirm, /cancel (모든 쓰기 작업은 확인 후 실행)
"""

import os
import re
import shutil
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes

# hermes_local 공통 유틸 임포트
from hermes_local import check_user, secure_path, ALLOWED_BASES

# FileManager (푸터 자동 추가)
from modules.file_manager import FileManager
_file_mgr = FileManager()

# ── 경로 정규화 (마크다운 특수문자 제거) ─────────────────────────
def _sanitize_path(raw: str) -> str:
    """입력 경로에서 마크다운 특수문자(백틱, 괄호)와 앞뒤 공백 제거."""
    cleaned = raw
    cleaned = re.sub(r'[`()]', '', cleaned)   # 백틱, 여는괄호, 닫는괄호 제거
    cleaned = cleaned.strip()                  # 앞뒤 공백 제거
    cleaned = cleaned.rstrip('.,')             # 후행 문장부호(. ,) 제거 (앞쪽은 보존)
    return cleaned

def _short_rel_path(full_path: Path) -> str:
    """허용 Base 중 하나에 상대적인 짧은 경로 반환"""
    for base in ALLOWED_BASES:
        try:
            return str(full_path.relative_to(base))
        except ValueError:
            continue
    return str(full_path)
# ─────────────────────────────────────────────────────────────────

# ── Action Realization Layer (Life-Harness Layer ❸) ────────────────────
from modules.action_realization_layer import ActionRealizationLayer
_action_layer = ActionRealizationLayer.get_instance()


# ═══════════════════════════════════════════════════════════════════
# 확인 시스템 — 쓰기 작업은 /confirm 후 실행
# ═══════════════════════════════════════════════════════════════════

def _queue_and_warn(update, context, *, description: str, action: str, **params):
    """보류 작업 등록 후 확인 안내 메시지 전송"""
    context.user_data['pending_file_op'] = {
        'action': action,
        'description': description,
        'params': params,
    }
    return (
        f"⚠️ **확인 필요**\n\n"
        f"{description}\n\n"
        f"✅ `/confirm` — 승인 후 실행\n"
        f"❌ `/cancel` — 취소"
    )


async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """마지막 보류 중인 파일 작업 승인 및 실행"""
    if not await check_user(update): return
    pending = context.user_data.pop('pending_file_op', None)
    if not pending:
        await update.message.reply_text("❓ 보류 중인 파일 작업이 없습니다.")
        return

    action = pending['action']
    params = pending['params']

    try:
        if action == 'create':
            file_path = params['path']
            content = params['content']
            # .md 파일은 FileManager 사용 (푸터 자동 추가)
            if str(file_path).endswith(".md"):
                try:
                    rel = os.path.relpath(str(file_path), _file_mgr.vault_path)
                    if not rel.startswith(".."):
                        _file_mgr.write_file(rel, content)
                        await update.message.reply_text(
                            f"📝 [생성 완료 + 푸터 추가] `{_short_rel_path(file_path)}`",
                            parse_mode="Markdown"
                        )
                        return
                except ValueError:
                    pass
            # vault 외부 또는 .md 아닌 파일 → 기존 방식
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            rel = _short_rel_path(file_path)
            await update.message.reply_text(
                f"📝 [생성 완료] `{rel}`", parse_mode="Markdown"
            )

        elif action == 'delete':
            src_path = params['path']
            trash_name = f"{src_path.name}_{int(os.path.getmtime(src_path))}" if src_path.exists() else src_path.name
            trash_path = Path.home() / ".Trash" / trash_name
            shutil.move(str(src_path), str(trash_path))
            rel = _short_rel_path(src_path)
            await update.message.reply_text(
                f"🗑️ [휴지통 이동 완료] `{rel}` → `~/.Trash/{trash_name}`\n"
                f"💡 복구: Finder 휴지통에서 `{trash_name}` 찾아 원위치로 이동",
                parse_mode="Markdown"
            )

        elif action == 'rename':
            old_path = params['old_path']
            new_path = params['new_path']
            new_path.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(old_path), str(new_path))
            rel_old = _short_rel_path(old_path)
            rel_new = _short_rel_path(new_path)
            await update.message.reply_text(
                f"✏️ [이름 변경 완료]\n"
                f"`{rel_old}` → `{rel_new}`", parse_mode="Markdown"
            )

        elif action == 'move':
            src_path = params['src_path']
            dest_path = params['dest_path']
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dest_path))
            rel_src = _short_rel_path(src_path)
            rel_dst = _short_rel_path(dest_path)
            await update.message.reply_text(
                f"🚚 [이동 완료]\n`{rel_src}` ➔ `{rel_dst}`", parse_mode="Markdown"
            )

        elif action == 'copy':
            src_path = params['src_path']
            dest_path = params['dest_path']
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_path), str(dest_path))
            rel_src = _short_rel_path(src_path)
            rel_dst = _short_rel_path(dest_path)
            await update.message.reply_text(
                f"📋 [복사 완료]\n`{rel_src}` ➔ `{rel_dst}`", parse_mode="Markdown"
            )

        else:
            await update.message.reply_text(f"❌ 알 수 없는 작업 유형: {action}")

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 실행 오류: {e}")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """마지막 보류 중인 파일 작업 취소"""
    if not await check_user(update): return
    pending = context.user_data.pop('pending_file_op', None)
    if not pending:
        await update.message.reply_text("❓ 취소할 보류 중인 작업이 없습니다.")
        return
    await update.message.reply_text(f"❌ 취소됨: {pending['description']}")


# ═══════════════════════════════════════════════════════════════════
# 명령어 핸들러
# ═══════════════════════════════════════════════════════════════════

# ── /create ──────────────────────────────────────────────────────────
async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update): return
    if len(context.args) < 1:
        await update.message.reply_text("⚠️ 사용법: `/create [경로/파일명] [내용...]`", parse_mode="Markdown")
        return

    path_str = _sanitize_path(context.args[0])
    content = " ".join(context.args[1:])

    # 🔒 Action Realization Layer 검증
    create_result = _action_layer.validate("file_create", path=path_str, content=content)
    if not create_result.valid:
        await update.message.reply_text(f"❌ {create_result.message}\n💡 {create_result.suggestion}")
        return

    try:
        file_path = secure_path(path_str)

        # ⚠️ 확인 대기열 등록
        msg = _queue_and_warn(update, context,
            description=f"📝 **파일 생성**: `{_short_rel_path(file_path)}`",
            action='create',
            path=file_path,
            content=content,
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {e}")


# ── /read (읽기 전용 — 확인 불필요) ──────────────────────────────────
async def cmd_read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update): return
    if len(context.args) < 1:
        await update.message.reply_text("⚠️ 사용법: `/read [폴더/파일명]`", parse_mode="Markdown")
        return

    target = _sanitize_path(' '.join(context.args))

    # 🔒 Action Realization Layer 검증
    read_result = _action_layer.validate("file_read", path=target)
    if not read_result.valid:
        await update.message.reply_text(f"❌ {read_result.message}\n💡 {read_result.suggestion}")
        return

    try:
        file_path = secure_path(target)
        if not file_path.exists() or not file_path.is_file():
            await update.message.reply_text("❌ 파일을 찾을 수 없습니다.")
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            await update.message.reply_text("⚠️ 텍스트(UTF-8) 형식이 아니거나 손상된 이진 파일입니다.")
            return

        if len(text) > 3500:
            truncated = text[:3500] + "\n\n...(이하 생략 - 파일 크기 초과)..."
            await update.message.reply_text(f"📖 {target} 내용 (일부):\n\n{truncated}", parse_mode=None)
        else:
            await update.message.reply_text(f"📖 {target} 내용:\n\n{text if text.strip() else '(빈 파일)'}", parse_mode=None)

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {e}")


# ── /move ────────────────────────────────────────────────────────────
async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update): return
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ 사용법: `/move [현재경로/파일명] [대상경로/파일명]`", parse_mode="Markdown")
        return

    src = _sanitize_path(' '.join(context.args[:-1]))
    dest_str = _sanitize_path(context.args[-1])

    # 🔒 검증
    move_result = _action_layer.validate("file_move_copy", source=src, destination=dest_str)
    if not move_result.valid:
        await update.message.reply_text(f"❌ {move_result.message}\n💡 {move_result.suggestion}")
        return

    try:
        src_path = secure_path(src)
        if not src_path.exists():
            await update.message.reply_text("❌ 원본 파일이 존재하지 않습니다.")
            return

        dest_dir = secure_path(dest_str)
        dest_path = dest_dir / src_path.name if dest_dir.is_dir() else dest_dir

        # ⚠️ 확인
        msg = _queue_and_warn(update, context,
            description=f"🚚 **파일 이동**: `{_short_rel_path(src_path)}` ➔ `{_short_rel_path(dest_path)}`",
            action='move',
            src_path=src_path,
            dest_path=dest_path,
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {e}")


# ── /copy ────────────────────────────────────────────────────────────
async def cmd_copy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update): return
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ 사용법: `/copy [원본경로/파일명] [대상경로/파일명]`", parse_mode="Markdown")
        return

    src = _sanitize_path(' '.join(context.args[:-1]))
    dest_str = _sanitize_path(context.args[-1])

    # 🔒 검증
    copy_result = _action_layer.validate("file_move_copy", source=src, destination=dest_str)
    if not copy_result.valid:
        await update.message.reply_text(f"❌ {copy_result.message}\n💡 {copy_result.suggestion}")
        return

    try:
        src_path = secure_path(src)
        if not src_path.exists():
            await update.message.reply_text("❌ 원본 파일이 존재하지 않습니다.")
            return

        dest_path = secure_path(dest_str)
        if dest_path.is_dir():
            dest_path = dest_path / src_path.name

        # ⚠️ 확인
        msg = _queue_and_warn(update, context,
            description=f"📋 **파일 복사**: `{_short_rel_path(src_path)}` ➔ `{_short_rel_path(dest_path)}`",
            action='copy',
            src_path=src_path,
            dest_path=dest_path,
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {e}")


# ── /delete (휴지통 이동) ──────────────────────────────────────────
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update): return
    if len(context.args) < 1:
        await update.message.reply_text("⚠️ 사용법: `/delete [경로/파일명]`", parse_mode="Markdown")
        return

    target = _sanitize_path(' '.join(context.args))

    # 🔒 검증
    delete_result = _action_layer.validate("file_delete", path=target)
    if not delete_result.valid:
        await update.message.reply_text(f"❌ {delete_result.message}\n💡 {delete_result.suggestion}")
        return

    try:
        file_path = secure_path(target)
        if not file_path.exists():
            await update.message.reply_text("❌ 파일이 존재하지 않습니다.")
            return

        # ⚠️ 확인 대기열 등록
        msg = _queue_and_warn(update, context,
            description=f"🗑️ **휴지통 이동**: `{_short_rel_path(file_path)}` (복구 가능)",
            action='delete',
            path=file_path,
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {e}")


# ── /rename ──────────────────────────────────────────────────────────
async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update): return
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ 사용법: `/rename [경로/파일명] [새이름]`", parse_mode="Markdown")
        return

    target = _sanitize_path(' '.join(context.args[:-1]))
    new_name = _sanitize_path(context.args[-1])

    # 🔒 검증
    rename_result = _action_layer.validate("file_rename", path=target, new_name=new_name)
    if not rename_result.valid:
        await update.message.reply_text(f"❌ {rename_result.message}\n💡 {rename_result.suggestion}")
        return

    try:
        old_path = secure_path(target)
        if not old_path.exists():
            await update.message.reply_text("❌ 파일이 존재하지 않습니다.")
            return

        new_path = old_path.parent / new_name
        # 새 경로도 같은 Base 내부여야 함 (부모와 같은 Base)
        try:
            for base in ALLOWED_BASES:
                new_path.relative_to(base)
                break
        except ValueError:
            await update.message.reply_text("🔒 새 이름이 허용 경로를 벗어납니다.")
            return

        # ⚠️ 확인 대기열 등록
        msg = _queue_and_warn(update, context,
            description=f"✏️ **이름 변경**: `{_short_rel_path(old_path)}` → `{_short_rel_path(new_path)}`",
            action='rename',
            old_path=old_path,
            new_path=new_path,
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {e}")


# ── /list (읽기 전용 — 확인 불필요) ─────────────────────────────────
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update): return

    # 기본 경로: args가 없으면 ALLOWED_BASES의 첫 번째 (vault)
    target = DEFAULT_LIST_DIR if len(context.args) < 1 else _sanitize_path(' '.join(context.args))

    # 🔒 검증
    list_result = _action_layer.validate("file_list", path=target)
    if not list_result.valid:
        await update.message.reply_text(f"❌ {list_result.message}\n💡 {list_result.suggestion}")
        return

    try:
        dir_path = secure_path(target)

        if not dir_path.exists():
            await update.message.reply_text("❌ 경로가 존재하지 않습니다.")
            return
        if not dir_path.is_dir():
            await update.message.reply_text("⚠️ 디렉토리가 아닙니다. 파일 경로 대신 폴더 경로를 입력해주세요.")
            return

        # 파일/폴더 목록 수집
        entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

        lines = []
        dir_count = 0
        file_count = 0
        for entry in entries:
            name = entry.name
            if entry.is_dir():
                lines.append(f"📁 `{name}/`")
                dir_count += 1
            else:
                size = entry.stat().st_size
                size_str = _format_size(size)
                lines.append(f"📄 `{name}` ({size_str})")
                file_count += 1

        if not lines:
            lines.append("(빈 폴더)")

        header = f"📂 **{_short_rel_path(dir_path)}/**\n"
        summary = f"\n\n📊 폴더 {dir_count}개, 파일 {file_count}개"

        # 텔레그램 메시지 길이 제한 (4096자)
        body = "\n".join(lines)
        full = header + body + summary
        if len(full) > 4000:
            # 긴 목록은 파일로 전송
            from pathlib import Path as P
            tmp_path = P(f"/tmp/hermes_list_{update.effective_chat.id}.txt")
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(f"{_short_rel_path(dir_path)}/\n" + "\n".join(
                    (f"[DIR] {e.name}" if e.is_dir() else f"[FILE] {e.name}  ({_format_size(e.stat().st_size)})")
                    for e in entries
                ))
            await update.message.reply_document(
                document=open(tmp_path, "rb"),
                filename=f"list_{dir_path.name}.txt",
                caption=f"📂 `{_short_rel_path(dir_path)}/` — {dir_count}개 폴더, {file_count}개 파일",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(full, parse_mode="Markdown")

    except PermissionError as e:
        await update.message.reply_text(f"🔒 보안 경고: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류 발생: {e}")


# ── 헬퍼 ─────────────────────────────────────────────────────────────
def _format_size(size_bytes: int) -> str:
    """바이트 크기를 사람이 읽기 쉬운 형태로"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024*1024):.1f}MB"

# list 기본 경로
DEFAULT_LIST_DIR = "."
