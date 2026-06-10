"""
modules/command_router.py
=========================
harness_agent.py handle_message() L239~345에서 분리 (2026-06-09).

역할:
  - /confirm, /cancel 파일 작업 확인/취소
  - 🔄 모드 전환 인라인 키보드 표시
  - @검색, @상태, @정리, @ingest 단축 명령어
  - Logic Engine 우선 처리 (파일 관련 키워드 있으면 스킵)

반환: True (처리 완료 → 상위에서 return) / False (LLM으로 넘김)

에러 진단:
  - ImportError: handlers._base → handlers/_base.py 존재 확인
  - AttributeError: logic.process_message → logic 객체 None 체크
  - AttributeError: ingest_inst.process_gardening → ingest_inst 초기화 확인
  - get_semantic() → semantic_search 모듈 로딩 실패 시 None 반환 주의
"""
import os
import shutil
import unicodedata
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

_FILE_KEYWORDS = [
    "파일", "폴더", "디렉토리", "리스트", "목록",
    "내용", "읽어", "보여줘", "열어줘", "확인",
    "LIST", "READ", "검색", "SEARCH",
    "찾아", "찾기", "알려줘",
    "삭제", "지워", "제거", "delete",
    "이동", "옮겨", "move",
    "복사", "copy", "카피",
    "이름변경", "rename",
    "생성", "만들어줘", "create",
    "저장", "save", "SAVE",
    "RUN_CMD", "WEB_READ",
]


async def route_command(
    update,
    context,
    user_text: str,
    *,
    file_op_pending: dict,
    safe_remove,
    get_current_mode,
    ALL_MODES,
    MODE_LABELS,
    sys_mon,
    memory,
    history,
    ingest_inst,
    get_semantic,
    logic,
    get_llm_response,
) -> bool:
    """명령어·단축어 처리. 처리됐으면 True, LLM에 넘겨야 하면 False."""

    # /confirm
    if user_text == "/confirm":
        pending = file_op_pending.get(update.effective_chat.id)
        if not pending:
            await update.message.reply_text("⚠️ 확인 대기 중인 작업이 없습니다.")
            return True
        action, path = pending["action"], pending["path"]
        dest = pending.get("dest")
        try:
            if action == "DELETE":
                result = safe_remove(path)
                await update.message.reply_text(f"✅ {result}")
            elif action == "MOVE":
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(path, dest)
                await update.message.reply_text(
                    f"✅ 이동 완료: `{path}` → `{dest}`", parse_mode="HTML"
                )
        except Exception as e:
            await update.message.reply_text(f"⚠️ 작업 실패: {e}")
        finally:
            file_op_pending.pop(update.effective_chat.id, None)
        return True

    # /cancel
    if user_text == "/cancel":
        if update.effective_chat.id in file_op_pending:
            file_op_pending.pop(update.effective_chat.id)
            await update.message.reply_text("❌ 작업이 취소되었습니다.")
        else:
            await update.message.reply_text("⚠️ 취소할 대기 작업이 없습니다.")
        return True

    # 🔄 모드 전환
    if user_text == "🔄 모드 전환":
        cur_mode = get_current_mode()
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅ ' if mode == cur_mode else '   '}{MODE_LABELS[mode]}",
                callback_data=f"mode_sel_{mode}",
            )]
            for mode in ALL_MODES
        ]
        await update.message.reply_text(
            f"⚙️ **현재 모드:** {MODE_LABELS.get(cur_mode, cur_mode)}\n\n"
            f"사용할 LLM 엔진을 선택해주세요.\n선택한 엔진이 실패하면 자동 폴백됩니다.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return True

    # @ 단축 명령어
    if user_text.startswith("@"):
        if user_text.startswith("@검색"):
            results = get_semantic().search(user_text.replace("@검색", "").strip())
            await update.message.reply_text(unicodedata.normalize("NFC", str(results)))
            return True
        if user_text == "@상태":
            _, msg = sys_mon.check_health()
            await update.message.reply_text(f"🤖 하네스 에이전트 상태\n{msg}")
            return True
        if user_text == "@정리":
            res = await memory.dream(history.history, llm_func=get_llm_response)
            await update.message.reply_text(res)
            return True
        if "@ingest" in user_text or "ingest해줘" in user_text:
            if ingest_inst:
                await update.message.reply_text("📂 지식 정원 가꾸기 시작... (Clippings → Wiki)")
                res = await ingest_inst.process_gardening()
                await update.message.reply_text(str(res))
            else:
                await update.message.reply_text("⚠️ IngestEngine이 초기화되지 않았습니다.")
            return True

    # Logic Engine 우선 처리 (파일 관련 키워드 있으면 LLM으로 넘김)
    if logic and not any(kw in user_text for kw in _FILE_KEYWORDS):
        try:
            logic_res = logic.process_message(user_text)
            if logic_res and "모르겠" not in logic_res and "이해하지 못" not in logic_res:
                history.add_message("user", user_text)
                history.add_message("assistant", logic_res)
                normalized = unicodedata.normalize("NFC", logic_res)
                from handlers._base import _reply_long
                prefix = "" if "안녕하세요, MJ 박사님!" in normalized else "⚡ [Logic]\n\n"
                await _reply_long(update.message, prefix + normalized)
                return True
        except Exception as e:
            print(f"⚠️ Logic Engine 처리 실패, LLM으로 전환: {e}")

    return False
