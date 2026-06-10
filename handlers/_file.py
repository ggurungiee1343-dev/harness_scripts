"""handlers._file — 파일/웹 명령어"""
import os
import re
from telegram import Update
from telegram.ext import ContextTypes
from handlers._base import (router, cove_engine_instance, _audit_engine,
    logger, add_to_history, _call_llm, _get_mem_info, check_user,
    secure_path, wiki_mgr, IngestEngine, _ingest_llm_wrapper, analyze_url,
    _reply_long, _edit_or_send_long)
from modules.optimistic_response import get_engine
from modules.weakness_miner import get_weakness_miner


def _warn_unsorted(report: str) -> str:
    """Ingest 리포트에서 Unsorted 비율을 확인하고 경고 메시지 반환"""
    unsorted = len(re.findall(r"Unsorted", report))
    total = len(re.findall(r"[✅❌📥]", report))  # 완료/실패/deferral 표시 개수
    if total == 0:
        return ""
    ratio = unsorted / total
    if ratio > 0.3:
        return (
            f"\n\n⚠️ **Ingest 알림**: 처리된 파일 중 {unsorted}/{total}개가 "
            f"Unsorted로 분류되었습니다 ({ratio:.0%}).\n"
            f"카테고리 확장 또는 재분류가 필요할 수 있습니다.\n"
            f"📥 `Inbox/` 폴더에서 pending 파일 확인 후 `/ingest` 재실행하세요."
        )
    return ""
async def cmd_web(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/web [URL] [질문] - 웹페이지 요약/분석"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ URL과 질문을 입력해 주세요. 예: `/web https://example.com 요약해줘`',
            parse_mode='HTML'
        )
        return

    url = context.args[0]
    question = ' '.join(context.args[1:]) if len(context.args) > 1 else '이 페이지를 요약해줘'

    try:
        safe_url = secure_path(url)  # URL 검증
        url_result = await analyze_url(url, question)
        ans = url_result
    except Exception as e:
        logger.error(f'Web Error: {e}')
        await get_weakness_miner().record_failure("cmd_web", str(e))
        ans = f'❌ 웹 분석 중 오류: {e}'

    await _reply_long(update.message, ans, parse_mode='HTML')

async def cmd_ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ingest [scan|interrogate] - Clippings 위키 자동 이관 또는 루트 방치 파일 스캔"""
    if not await check_user(update):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    subcmd = context.args[0] if context.args else None
    opt = get_engine()

    if subcmd == 'scan':
        async def _work_scan():
            from config import WIKI_DEST_DIR
            engine = IngestEngine(source_dir="", dest_dir=str(WIKI_DEST_DIR), llm_func=_ingest_llm_wrapper)
            report = await engine.scan_root_only()
            warn = _warn_unsorted(report)
            # 결과가 길어서 별도 메시지로 전송
            await _reply_long(update.effective_message, report + warn, parse_mode='HTML')
            return {"summary": f"루트 스캔 완료"}

        await opt.initiate_action(
            bot=context.bot,
            chat_id=chat_id,
            user_id=user_id,
            action_name="ingest_scan",
            description="🔍 루트 파일 스캔 중... (이동 없이 태그+요약만 추가)",
            work_fn=_work_scan,
        )
        return

    if subcmd == 'interrogate':
        async def _work_interrogate():
            from config import CLIPPINGS_DIR, WIKI_DEST_DIR
            engine = IngestEngine(source_dir=str(CLIPPINGS_DIR), dest_dir=str(WIKI_DEST_DIR), llm_func=_ingest_llm_wrapper)
            report = await engine.process_all(interrogate=True)
            warn = _warn_unsorted(report)
            await _reply_long(update.effective_message, report + warn, parse_mode='HTML')
            return {"summary": "질문 생성 모드 완료"}

        await opt.initiate_action(
            bot=context.bot,
            chat_id=chat_id,
            user_id=user_id,
            action_name="ingest_interrogate",
            description="🤔 질문 생성 모드로 Clippings 처리 중...",
            work_fn=_work_interrogate,
        )
        return

    # 기본 모드: Clippings/ → Wiki
    async def _work_ingest():
        from config import CLIPPINGS_DIR, WIKI_DEST_DIR
        engine = IngestEngine(source_dir=str(CLIPPINGS_DIR), dest_dir=str(WIKI_DEST_DIR), llm_func=_ingest_llm_wrapper)
        report = await engine.process_all()
        warn = _warn_unsorted(report)
        await _reply_long(update.effective_message, report + warn, parse_mode='HTML')
        processed = len(re.findall(r'[✅❌📥]', report))
        return {"summary": f"{processed}개 파일 처리", "processed": processed}

    await opt.initiate_action(
        bot=context.bot,
        chat_id=chat_id,
        user_id=user_id,
        action_name="ingest",
        description="🌱 Clippings → Wiki 이관 중...",
        work_fn=_work_ingest,
    )

async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/recent - 옵시디언 위키 최근 수정 문서 목록"""
    if not await check_user(update):
        return

    try:
        recent_ctx = wiki_mgr.get_recent_context() if hasattr(wiki_mgr, 'get_recent_context') else '📋 최근 변경 내역을 가져올 수 없습니다.'
        await _reply_long(update.message, recent_ctx, parse_mode='HTML')
    except Exception as e:
        logger.error(f'Recent error: {e}')
        await get_weakness_miner().record_failure("cmd_recent", str(e))
        await update.message.reply_text(f'❌ 최근 문서 조회 중 오류: {e}')
