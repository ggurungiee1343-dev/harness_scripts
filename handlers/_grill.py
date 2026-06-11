"""handlers._grill — 문서 기반 대화 (/grill-with-docs)"""
import os
import unicodedata
from telegram import Update
from telegram.ext import ContextTypes
from modules.weakness_miner import get_weakness_miner
from handlers._base import (router, logger, add_to_history, _call_llm, safe_reply, safe_edit,
    check_user, secure_path)
from hermes_local import BASE_DIR

async def cmd_grill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/grill [문서경로] [질문] — Vault 문서를 읽고 LLM 기반 Q&A"""
    if not await check_user(update):
        return

    # 인자 파싱: /grill path/to/doc.md 질문...
    # or /grill "path/to/doc.md" 긴 질문...
    # or /grill docname (wiki 검색)
    if not context.args:
        await safe_reply(update.message, 
            "🔥 **Grill with Docs — 문서 기반 대화**\n\n"
            "명령어: `/grill [문서경로/키워드] [질문]`\n\n"
            "예시:\n"
            "• `/grill wiki/00_Meta/시스템_상태.md 요약해줘`\n"
            "• `/grill 시스템 상태 현재 상태 알려줘` (키워드 검색)\n"
            "• `/grill hot.md 오늘 뭐 있었어`",
            parse_mode='HTML'
        )
        return

    args = context.args
    # 마지막 인자가 질문, 나머지는 문서경로
    if len(args) < 2:
        # 질문 없음 → 문서 내용만 요약
        doc_query = args[0]
        question = "이 문서를 요약해줘"
    else:
        doc_query = args[0]
        question = ' '.join(args[1:])

    msg = await safe_reply(update.message, 
        f"🔥 **Grill:** `{doc_query}` 검색 중...",
        parse_mode='HTML'
    )

    # 1. 문서 경로 찾기
    doc_path = _resolve_doc_path(doc_query)
    if not doc_path:
        await safe_edit(msg, 
            f"❌ 문서를 찾을 수 없습니다: `{doc_query}`\n"
            "폴더 경로를 포함해 주세요 (예: `wiki/00_Meta/파일명.md`)",
            parse_mode='HTML'
        )
        return

    # 2. 문서 읽기
    try:
        with open(doc_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        await safe_edit(msg, f"❌ 문서 읽기 오류: `{e}`", parse_mode='HTML')
        return

    rel_path = os.path.relpath(doc_path, str(BASE_DIR))
    file_size_kb = len(content.encode('utf-8')) / 1024

    # 3. 문서가 너무 크면 앞부분만
    max_chars = 15000
    if len(content) > max_chars:
        truncated = True
        doc_section = content[:max_chars]
        doc_section += f"\n\n... [문서가 {file_size_kb:.0f}KB로 너무 큽니다. 처음 {max_chars//1000}K자만 분석]"
    else:
        truncated = False
        doc_section = content

    await safe_edit(msg, 
        f"🔥 **Grill:** `{rel_path}` ({file_size_kb:.0f}KB)\n"
        f"💬 {question}",
        parse_mode='HTML'
    )

    # 4. LLM으로 질문 응답 생성
    prompt = (
        f"다음은 Obsidian Vault 문서 `{rel_path}`의 내용입니다.\n\n"
        f"--- 문서 내용 시작 ---\n"
        f"{doc_section}\n"
        f"--- 문서 내용 끝 ---\n\n"
        f"위 문서를 바탕으로 다음 질문에 답변해 주세요:\n"
        f"{question}\n\n"
        f"문서에 없는 내용은 '문서에 해당 정보가 없습니다'라고 말해주세요."
    )

    await update.message.reply_chat_action("typing")

    try:
        answer = await _call_llm(prompt)
    except Exception as e:
        await get_weakness_miner().record_failure("cmd_grill", str(e))
        answer = f"❌ LLM 응답 생성 오류: `{e}`"

    # 응답 길이 제한
    if len(answer) > 3500:
        answer = answer[:3500] + "\n\n...(계속)..."

    result = (
        f"📄 **{rel_path}**\n\n"
        f"{answer}\n\n"
        f"💬 `{question}`"
    )

    await safe_edit(msg, result, parse_mode='HTML')

    await add_to_history("assistant",
        f"[Grill] {rel_path} ({file_size_kb:.0f}KB): {question[:60]}..."
    )

def _resolve_doc_path(query: str) -> str:
    """문서 경로를 다양한 형태로 찾기"""
    vault = str(BASE_DIR)

    # 1. 절대경로 시도
    if query.startswith('/'):
        candidate = os.path.normpath(query)
        if os.path.isfile(candidate):
            return candidate

    # 2. 상대경로 시도 (vault 기준)
    normalized = unicodedata.normalize('NFC', query)
    candidate = os.path.join(vault, normalized)
    if os.path.isfile(candidate):
        return candidate

    # 3. .md 확장자 자동 추가
    if not query.endswith('.md'):
        candidate = os.path.join(vault, normalized + '.md')
        if os.path.isfile(candidate):
            return candidate
        candidate = os.path.join(vault, 'wiki', normalized + '.md')
        if os.path.isfile(candidate):
            return candidate

    # 4. 키워드 검색 (파일명 fuzzy match)
    query_lower = query.lower().replace(' ', '_')
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {
            '.obsidian', '.smart-env', '.tmp.drivedownload', '.tmp.driveupload',
            '.vscode', 'graphify-out', 'outputs', 'raw', '.git'}]
        for f in files:
            if not f.endswith('.md') or f.startswith('.'):
                continue
            if query_lower in f.lower().replace(' ', '_'):
                return os.path.join(root, f)

    return None
