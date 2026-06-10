"""
hermes_handlers.py — 메인 명령어, 인텔리전스 및 지식 연동 핸들러
==============================================================
웰컴, 도움말, 질문(/ask, /cove, /web), 지식이관(/ingest),
드러밍(/dreaming), 배시(/exec), 핫클립(/clip) 등을 처리합니다.
"""

import os
import sys
import uuid
import logging
import datetime
import shutil
import signal
import subprocess
import asyncio
from pathlib import Path
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from modules.kanban_manager import KanbanDB
from modules.audit_engine import AuditEngine
from telegram.ext import ContextTypes

# === 감사 엔진 인스턴스 ===
_audit_engine = AuditEngine()

# === 경로 등록 ===
sys.path.append('/Users/bluesea/.hermes/plugins')
sys.path.append('/Users/bluesea/.hermes/skills/knowledge/wiki/scripts')
sys.path.append('/Users/bluesea/.hermes/skills/brain/cognitive/scripts')
sys.path.append('/Users/bluesea/.hermes/skills/devops/executor/scripts')
sys.path.append('/Users/bluesea/Applications/Mjauto/Scripts/modules')

# === 모듈 임포트 ===
from hybrid_router import router
from wiki_manager import WikiManager
from verification_engine import verifier
from cove_engine import cove_engine_instance
from executor import execute_bash_command
from ingest_engine import IngestEngine
from memory_engine import MemoryEngine
from system_monitor import SystemMonitor
from web_reader import analyze_url
from modules.action_realization_layer import ActionRealizationLayer
action_layer = ActionRealizationLayer.get_instance()
from hermes_local import check_user, secure_path, history_mgr, wiki_mgr, BASE_DIR, PENDING_TASKS, _make_keyboard

# === 로거 ===
logger = logging.getLogger('HermesOrchestrator')


# ======================================================================
# add_to_history
# ======================================================================
async def add_to_history(role: str, content: str) -> None:
    """대화 히스토리 + Bio-Memory 동시 저장"""
    history_mgr.add_message(role, content)
    try:
        from hermes_memory_patch import bio_add_message
        bio_add_message(role, content)
    except Exception as e:
        logger.error(f'❌ Bio-Memory 기록 실패: {e}')


# ======================================================================
# cmd_start
# ======================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """웰컴 메시지 및 메뉴 버튼 배치"""
    if not await check_user(update):
        return

    welcome_text = (
        '👋 안녕하십니까, 박사님! **Harness V2.5 자율 관제 센터**에 오신 것을 환영합니다.\n\n'
        '옵시디언 위키 교차 검증과 AI 자율 에러 수정 엔진이 백그라운드에 완벽하게 빌드되었습니다.\n'
        '아래 버튼 메뉴나 명령어를 사용하여 관제를 시작해 주십시오.'
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=_make_keyboard(),
        parse_mode='Markdown'
    )


# ======================================================================
# cmd_help
# ======================================================================
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """명령어 도움말 출력"""
    if not await check_user(update):
        return

    help_text = (
        '🤖 **헤르메스 V2.5 명령어 가이드**\n\n'
        '🔹 **인텔리전스 & 팩트체크**\n'
        '• `/ask [질문 내용]` - (기본) 빠른 답변 및 위키 기반 Q&A\n'
        '• `/cove [질문 내용]` - Gemma4 CoVe 팩트체크 4단계 심층 분석\n'
        '• `/clip [질문]` - 클립보드 내용 캡처 및 질문 분석\n'
        '• `/web [URL] [질문]` - 입력한 인터넷 주소의 내용을 요약/분석\n'
        '• `/search [검색어]` - 웹 브라우징 검색 및 결과 요약\n'
        '• `/readweb [URL]` - 웹페이지 내용을 읽어서 요약\n'
        '• `/searchpaper [검색어]` - OpenAlex API 기반 학술 논문 검색 (법학 포함 전 분야)\n'
        '• `/exec [명령어]` - AI 자율 에러 수정 기능이 탑재된 Bash 실행\n\n'

        '🧠 **메모리 & 학습**\n'
        '• `/memory` - 💡 3계층 메모리 상태 (Deriver + Dialectic + Dreamer)\n'
        '• `/memory_search [키워드]` - 🔍 L2/L3 통합 메모리 검색\n'
        '• `/memory_dream` - 🌙 L1→L2→L3 메모리 승격 Dreaming 실행\n'
        '• `/memory_audit` - 📋 메모리 3계층 종합 감사 (Layer/Source/Expiry)\n'
        '• `/dreaming` - 📝 대화/작업 → Journal/Memory/hot.md 자동 분배\n\n'

        '🪴 **지식 정원 & 동기화**\n'
        '• `/ingest` - Clippings 신규 파일을 위키로 자동 분류 이관 (원본 Archive 보존)\n\n'

        '🛡️ **하네스 컨트롤**\n'
        '• `/harness` - ACID 하네스 상태 조회 및 제어 설정\n'
        '• `/hdod` - 하네스 DoD(Defense-in-Depth) 진단\n'
        '• `/hstatus` - 시스템 진단 리포트 (CPU/메모리/디스크/LLM)\n'
        '• `/hrollback` - 하네스 의사결정 롤백 실행\n\n'

        '⚙️ **세션 설정**\n'
        '• `/profile [모드]` - 작업 모드 변경 (writer/analyst/default)\n\n'

        '📁 **파일 관리**\n'
        '• `/read [경로]` - 파일 안전 읽기\n'
        '• `/create [파일명] [내용]` - 파일 생성\n'
        '• `/move [현재경로] [대상폴더]` - 파일 안전 이동\n'
        '• `/copy [현재경로] [대상경로]` - 파일 안전 복사\n'
        '• `/delete [경로]` - 파일 휴지통 이동 (삭제)\n'
        '• `/rename [경로] [새이름]` - 파일 이름 변경\n'
        '• `/list [경로]` - 디렉토리 파일 목록\n\n'

        '📊 **모니터링 & 상태**\n'
        '• `/recent` - 옵시디언 위키 최근 수정 문서 5개 목록 및 요약 확인\n'
        '• `/status` - 실제 OS 여유 메모리 진입 및 시스템 핫토픽 점검'
    )

    await update.message.reply_text(help_text, parse_mode='Markdown')


# ======================================================================
# cmd_ask
# ======================================================================
async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ask 질문 - CoVe 팩트체크 팩터 실행"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ 질문을 함께 입력해 주세요. 예: `/ask 세종대왕의 업적을 요약해줘`',
            parse_mode='Markdown'
        )
        return

    question = ' '.join(context.args)
    await add_to_history('user', question)

    # 히스토리 조회
    try:
        if history_mgr:
            history_data = history_mgr.get_history_for_llm()[-10:]
        else:
            history_data = []
    except Exception:
        history_data = []

    # CoVe 실행
    verified_ans, pending_actions = await verifier.process_query(
        question, history_data=history_data
    )

    await add_to_history('assistant', verified_ans)

    # 승인 대기 액션이 있으면 버튼 생성
    if pending_actions:
        keyboard = []
        for action in pending_actions:
            task_id = str(uuid.uuid4())[:8]
            PENDING_TASKS[task_id] = action

            action_name = action.get('action', '')
            args = action.get('args', {})

            if action_name == 'move_file':
                target = args.get('source_path') or args.get('path', '')
                btn_text = f"📦 이동 승인: {Path(target).name}"
            elif action_name == 'delete_file':
                target = args.get('path', '')
                btn_text = f"🗑️ 삭제 승인: {Path(target).name}"
            else:
                target = args.get('path', '파일')
                btn_text = f"✅ {action_name} 승인: {target}"

            keyboard.append([
                InlineKeyboardButton(
                    btn_text,
                    callback_data=task_id
                )
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f'{verified_ans}\n\n📋 **승인 대기 작업:**\n위 버튼을 눌러 작업을 승인하거나 거절하세요.',
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            verified_ans,
            parse_mode='Markdown'
        )


# ======================================================================
# cmd_cove
# ======================================================================
async def cmd_cove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cove [devil] 질문 - 독립형 팩트체크 (devil: 반론 생성 모드)"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ 질문을 입력해 주세요. 예: `/cove 이순신 장군의 주요 업적은?`\n'
            '🔄 `devil` 모드: `/cove devil <주장>` — 반론 생성',
            parse_mode='Markdown'
        )
        return

    # [8] devil 모드 — 반론 생성
    is_devil = context.args[0].lower() == 'devil'
    if is_devil:
        if len(context.args) < 2:
            await update.message.reply_text(
                '⚠️ 반론 생성 모드입니다. 주장을 입력해 주세요.\n'
                '예: `/cove devil AI가 인류를 멸망시킬 것이다`',
                parse_mode='Markdown'
            )
            return
        question = ' '.join(context.args[1:])
        await add_to_history('user', f'[devil] {question}')

        try:
            verified_ans, _ = await cove_engine_instance.process_query(
                f'다음 주장에 대한 반론(counterargument)을 생성하세요: "{question}"\n'
                f'해당 주장의 취약점을 지적하고, 반대 증거나 논리를 제시하세요.',
                mode='strict'
            )
            header = f"⚔️ **Devil's Advocate — 반론 생성**\n📌 원 주장: `{question}`\n\n"
            reply = header + verified_ans
        except Exception as e:
            logger.error(f'CoVe Devil Error: {e}')
            reply = f'❌ 반론 생성 중 에러 발생: {e}'

        await add_to_history('assistant', reply)
        await update.message.reply_text(reply, parse_mode='Markdown')
        return

    question = ' '.join(context.args)
    await add_to_history('user', question)

    try:
        verified_ans, _ = await cove_engine_instance.process_query(
            question, mode='strict'
        )
    except Exception as e:
        logger.error(f'CoVe Error: {e}')
        verified_ans = f'❌ CoVe 실행 중 에러 발생: {e}'

    await add_to_history('assistant', verified_ans)
    await update.message.reply_text(verified_ans, parse_mode='Markdown')


# ======================================================================
# cmd_web
# ======================================================================
async def cmd_web(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/web [URL] [질문] - 웹페이지 요약/분석"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ URL과 질문을 입력해 주세요. 예: `/web https://example.com 요약해줘`',
            parse_mode='Markdown'
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
        ans = f'❌ 웹 분석 중 오류: {e}'

    await update.message.reply_text(
        ans,
        parse_mode='Markdown'
    )


# ======================================================================
# cmd_ingest
# ======================================================================
async def cmd_ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ingest - Clippings 위키 자동 이관"""
    if not await check_user(update):
        return

    msg = await update.message.reply_text('🔄 Clippings 파일 이관 작업을 시작합니다...')

    try:
        # IngestEngine 인스턴스 생성 (모듈 레벨이 아닌 로컬)
        ingest_engine = IngestEngine()
        report = await ingest_engine.process_all()
        await msg.edit_text(report, parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Ingest Error: {e}')
        await msg.edit_text(f'❌ Ingest 실행 중 오류 발생: {e}')


# ======================================================================
# execute_dreaming_job
# ======================================================================
async def execute_dreaming_job(context, chat_id: int) -> None:
    """Dreaming 백그라운드 작업 (async_llm_func 활용)"""
    try:
        from memory_engine import async_llm_func
        # L1→L2→L3 메모리 승격 Dreaming
        history_data = history_mgr.get_history_for_llm()[-20:] if history_mgr else []
        engine = MemoryEngine()
        res = await engine.run_dreaming(
            history=history_data,
            async_llm_func=async_llm_func
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=res,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Dreaming job error: {e}')
        await context.bot.send_message(
            chat_id=chat_id,
            text=f'❌ Dreaming 백그라운드 작업 실패: {e}'
        )


# ======================================================================
# cmd_dreaming
# ======================================================================
async def cmd_dreaming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dreaming - 대화/작업 → Journal/Memory/hot.md 자동 분배"""
    if not await check_user(update):
        return

    msg = await update.message.reply_text('🌙 Dreaming 엔진을 구동합니다...')

    try:
        from memory_engine import async_llm_func
        history_data = history_mgr.get_history_for_llm()[-20:] if history_mgr else []
        engine = MemoryEngine()
        res = await engine.run_dreaming(
            history=history_data,
            async_llm_func=async_llm_func
        )
        await msg.edit_text(res, parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Dreaming Error: {e}')
        await msg.edit_text(f'❌ Dreaming 실행 중 오류 발생: {e}')


# ======================================================================
# cmd_exec
# ======================================================================
async def cmd_exec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/exec 명령어 - 자율 에러 복구형 Bash 실행 (SKILL.md 자동 학습)"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ 실행할 명령어를 함께 입력해 주세요. 예: `/exec ls -la`',
            parse_mode='Markdown'
        )
        return

    cmd = ' '.join(context.args)
    msg = await update.message.reply_text(
        f'⚙️ **명령어 자율 실행 중...**\n'
        f'에러 발생 시 Gemma4가 최대 3회까지 수정하여 재실행하며,\n'
        f'복구 성공 시 SKILL.md가 자동으로 학습 DB에 저장됩니다.\n'
        f'`{cmd}`',
        parse_mode='Markdown'
    )

    try:
        exec_res = await execute_bash_command(cmd)
        # 결과 포맷
        stdout = exec_res.get('stdout', '')
        stderr = exec_res.get('stderr', '')
        exit_code = exec_res.get('exit_code', -1)

        result_text = f'📋 **실행 결과 (Exit: {exit_code})**\n\n'
        if stdout:
            result_text += f'```\n{stdout[:2000]}\n```\n'
        if stderr:
            result_text += f'⚠️ **Stderr:**\n```\n{stderr[:1000]}\n```\n'
        if stdout and len(stdout) > 2000:
            result_text += '\n...(이하 생략 - 로그 길이가 너무 깁니다)...'

        await msg.edit_text(result_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Exec error: {e}')
        await msg.edit_text(
            f'❌ 실행 중 치명적 예외 발생: {e}',
            parse_mode='Markdown'
        )


# ======================================================================
# cmd_clip
# ======================================================================
async def cmd_clip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clip [내용] - 텍스트를 Clippings 폴더에 .md 파일로 즉시 저장"""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            '⚠️ 사용법: `/clip [저장할 내용]`\n'
            '예: `/clip 양자역학에서 얽힘(entanglement)이란 두 입자의 상태가 서로 연결된 현상을 말한다.`',
            parse_mode='Markdown'
        )
        return

    content = ' '.join(context.args)
    now = datetime.datetime.now()
    date_str = now.strftime('%Y-%m-%d %H:%M')
    timestamp = now.strftime('%Y%m%d_%H%M%S')

    clippings_dir = '/Users/bluesea/Applications/Mjobsidian/Clippings'
    os.makedirs(clippings_dir, exist_ok=True)

    # 파일명 생성 (첫 20자 추출)
    first_line = content.split('\n')[0][:40].strip()
    safe_name = ''.join(c if c.isalnum() or c in ' _-.' else '_' for c in first_line)
    filename = f'clip_{timestamp}_{safe_name[:20]}.md'
    clip_path = os.path.join(clippings_dir, filename)

    md_content = (
        f'# Clipping ({date_str})\n\n'
        f'{content}\n\n'
        f'---\n'
        f'*텔레그램 /clip 으로 자동 저장*\n'
    )

    try:
        with open(clip_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        await update.message.reply_text(
            f'📎 **Clipping 저장 완료!**\n'
            f'📁 위치: `Clippings/{filename}`\n'
            f'⏰ {date_str}',
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Clip error: {e}')
        await update.message.reply_text(
            f'❌ 저장 중 오류 발생: {e}'
        )


# ======================================================================
# cmd_recent
# ======================================================================
async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/recent - 옵시디언 위키 최근 수정 문서 목록"""
    if not await check_user(update):
        return

    try:
        recent_ctx = wiki_mgr.get_recent_changes(limit=5) if hasattr(wiki_mgr, 'get_recent_changes') else '📋 최근 변경 내역을 가져올 수 없습니다.'
        await update.message.reply_text(recent_ctx, parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Recent error: {e}')
        await update.message.reply_text(f'❌ 최근 문서 조회 중 오류: {e}')


# ======================================================================
# cmd_status
# ======================================================================
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status - 시스템 상태 및 핫토픽 점검"""
    if not await check_user(update):
        return

    try:
        sys_monitor = SystemMonitor()
        health_ok, health_msg = sys_monitor.check_health()

        report = f'📊 **시스템 진단 리포트**\n\n'
        report += f'🩺 **건강 상태:** {"✅ 정상" if health_ok else "⚠️ 주의"}\n'
        report += f'{health_msg}\n\n'

        # 핫토픽 파일 확인
        status_file = os.path.join(BASE_DIR, '..', 'wiki', '00_Meta', 'hot.md')
        status_file = os.path.abspath(status_file)

        if os.path.exists(status_file):
            with open(status_file, 'r', encoding='utf-8') as f:
                content = f.read(500)
            report += f'🔥 **핫토픽:**\n{content[:300]}'
        else:
            report += '🔥 핫토픽 파일 없음'

        await update.message.reply_text(report, parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Status error: {e}')
        await update.message.reply_text(f'❌ 상태 조회 중 오류: {e}')

# ======================================================================
# cmd_goal
# ======================================================================
async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/goal [목표] - 장기 목표 설정 (Dreaming 시 헌법과 함께 감사)"""
    if not await check_user(update):
        return

    goal_file = os.path.expanduser('~/.hermes/active_goal.txt')
    
    if not context.args:
        if os.path.exists(goal_file):
            with open(goal_file, 'r', encoding='utf-8') as f:
                current_goal = f.read().strip()
            msg = f"🎯 **현재 설정된 목표:**\n{current_goal}\n\n목표를 새로 설정하려면 `/goal [새 목표]`를 입력하고, 삭제하려면 `/goal clear`를 입력하세요."
        else:
            msg = "⚠️ 현재 설정된 장기 목표가 없습니다.\n목표를 설정하려면 `/goal [목표 내용]`을 입력하세요."
        await update.message.reply_text(msg, parse_mode='Markdown')
        return

    subcmd = context.args[0].lower()
    if subcmd == 'clear':
        if os.path.exists(goal_file):
            os.remove(goal_file)
            await update.message.reply_text("🗑️ 현재 설정된 목표가 삭제되었습니다.", parse_mode='Markdown')
        else:
            await update.message.reply_text("⚠️ 삭제할 목표가 없습니다.", parse_mode='Markdown')
        return

    new_goal = ' '.join(context.args)
    try:
        os.makedirs(os.path.dirname(goal_file), exist_ok=True)
        with open(goal_file, 'w', encoding='utf-8') as f:
            f.write(new_goal)
        await update.message.reply_text(f"✅ **새로운 장기 목표가 설정되었습니다.**\n\n🎯 {new_goal}\n\n(오늘 밤 Dreaming 스케줄러가 이 목표의 진척도와 헌법 준수 여부를 평가합니다.)", parse_mode='Markdown')
    except Exception as e:
        logger.error(f'Goal command error: {e}')
        await update.message.reply_text(f'❌ 목표 설정 중 오류 발생: {e}')


# ======================================================================
# [7] cmd_delegate — delegate_task: 핸들러 내부 비동기 task 분리
# ======================================================================
_delegate_tasks = {}

async def cmd_delegate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delegate [작업 설명] — 백그라운드 비동기 위임 실행 + audit 기록"""
    if not await check_user(update):
        return

    if not context.args:
        if not _delegate_tasks:
            await update.message.reply_text(
                "📋 현재 실행 중인 delegate 작업이 없습니다.\n"
                "사용법: `/delegate <작업 설명>`",
                parse_mode='Markdown'
            )
        else:
            lines = ["📋 **실행 중인 delegate 작업:**\n"]
            for tid, info in list(_delegate_tasks.items()):
                lines.append(f"• `{tid}`: {info.get('description', '?')} — {info.get('status', '?')}")
            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
        return

    description = ' '.join(context.args)
    task_id = str(uuid.uuid4())[:8]

    msg = await update.message.reply_text(
        f"🔄 **delegate 작업 시작**\n📋 `{task_id}`: {description}\n⏳ 실행 중...",
        parse_mode='Markdown'
    )

    async def _run_delegate(tid: str, desc: str, chat_id: int, message_id: int):
        try:
            _delegate_tasks[tid] = {"description": desc, "status": "running", "started": str(datetime.datetime.now())}
            result_lines = [f"📋 **delegate 결과 ({tid})**\n"]
            result_lines.append(f"📝 작업: {desc}\n")

            # ── 실행 전략 판별 ────────────────────────────────────────
            # bash 명령어 패턴: 파이프, 리다이렉션, 일반적인 CLI 명령
            import re
            _shell_pattern = re.compile(
                r'^(ls|cat|ps|df|du|top|htop|free|uname|whoami|id|pwd|echo|'
                r'grep|find|head|tail|wc|sort|cut|tr|diff|ping|curl|wget|'
                r'git|python|pip|npm|node|docker|brew|chmod|chown|mkdir|'
                r'rmdir|cp|mv|rm|kill|nohup|systemctl|launchctl|journalctl|'
                r'dmesg|ifconfig|ip|netstat|ss|scp|ssh|rsync|tar|gzip|'
                r'python3|node|hermes|htop)'
            )
            first_word = desc.strip().split()[0] if desc.strip() else ""
            is_shell = bool(_shell_pattern.match(first_word)) or '|' in desc or '>' in desc or '&&' in desc

            if is_shell:
                # ── 실행 경로 1: Bash 명령어 실행 ─────────────────────
                result_lines.append(f"💻 **실행**: `{desc}`\n")
                await context.bot.edit_message_text(
                    "\n".join(result_lines) + "\n⏳ 실행 중...",
                    chat_id=chat_id, message_id=message_id, parse_mode='Markdown'
                )
                exec_res = await execute_bash_command(desc)
                stdout = exec_res.get('stdout', '')
                stderr = exec_res.get('stderr', '')
                exit_code = exec_res.get('exit_code', -1)

                if exit_code == 0:
                    _delegate_tasks[tid]["status"] = "completed"
                    result_lines.append(f"✅ 종료 코드: `{exit_code}`\n")
                    if stdout:
                        # stdout 길이 제한 (3000자)
                        _out = stdout[:3000]
                        if len(stdout) > 3000:
                            _out += "\n\n… (출력이 잘렸습니다)"
                        result_lines.append(f"```\n{_out}\n```")
                else:
                    _delegate_tasks[tid]["status"] = f"error(exit={exit_code})"
                    result_lines.append(f"❌ 종료 코드: `{exit_code}`")
                    if stderr:
                        result_lines.append(f"```\n{stderr[:2000]}\n```")
                    if stdout:
                        result_lines.append(f"```\n{stdout[:1000]}\n```")

                _audit_engine.log_audit("delegate", tid, {
                    "description": desc,
                    "type": "bash",
                    "exit_code": exit_code,
                    "status": "completed" if exit_code == 0 else "error"
                })
            else:
                # ── 실행 경로 2: LLM 위임 ─────────────────────────────
                result_lines.append(f"🧠 **LLM 위임 처리 중...**\n")
                await context.bot.edit_message_text(
                    "\n".join(result_lines) + "\n⏳ LLM 응답 대기 중...",
                    chat_id=chat_id, message_id=message_id, parse_mode='Markdown'
                )
                llm_messages = [
                    {"role": "system", "content": "당신은 Hermes 시스템의 delegate 작업 에이전트입니다. 주어진 작업을 분석하고 실행 결과 또는 답변을 제공하세요. 가능하면 구체적인 실행 계획과 결과를 포함하세요."},
                    {"role": "user", "content": f"다음 delegate 작업을 처리해주세요:\n\n{desc}"}
                ]
                llm_result = await router.route_and_execute(llm_messages)
                result_text = str(llm_result)[:3500]
                if len(str(llm_result)) > 3500:
                    result_text += "\n\n… (응답이 잘렸습니다)"

                result_lines.append(f"🧠 **LLM 응답**\n\n{result_text}")
                _delegate_tasks[tid]["status"] = "completed"

                _audit_engine.log_audit("delegate", tid, {
                    "description": desc,
                    "type": "llm",
                    "response_length": len(str(llm_result)),
                    "status": "completed"
                })

            _delegate_tasks[tid]["result"] = result_lines[-1][:200] if result_lines else ""
            await context.bot.edit_message_text(
                "\n".join(result_lines),
                chat_id=chat_id, message_id=message_id, parse_mode='Markdown'
            )

        except Exception as e:
            logger.error(f"Delegate task {tid} error: {e}", exc_info=True)
            _delegate_tasks[tid]["status"] = f"error: {e}"
            try:
                await context.bot.edit_message_text(
                    f"❌ delegate 작업 `{tid}` 실패: {e}",
                    chat_id=chat_id, message_id=message_id, parse_mode='Markdown'
                )
            except Exception:
                pass
            _audit_engine.log_audit("delegate", tid, {
                "description": desc, "status": "error", "error": str(e)
            })
        finally:
            await asyncio.sleep(300)
            _delegate_tasks.pop(tid, None)

    asyncio.create_task(_run_delegate(task_id, description, update.effective_chat.id, msg.message_id))


async def cmd_kanban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kanban command - simple Kanban board management."""
    if not await check_user(update):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ 사용법: `/kanban [list|add|move|delete] ...`\n"
            "`list` - 현재 보드 표시\n"
            "`add <title>` - 새 카드 추가 (TODO)\n"
            "`move <id> <column>` - 카드 이동 (TODO/IN_PROGRESS/DONE)\n"
            "`delete <id>` - 카드 삭제",
            parse_mode='Markdown'
        )
        return

    subcmd = context.args[0].lower()
    db = KanbanDB()
    if subcmd == "list":
        board = db.list_cards()
        # Build message with inline buttons for each card
        text = "*🗂️ Kanban 보드*\n\n"
        keyboard = []
        for col in ("TODO", "IN_PROGRESS", "DONE"):
            text += f"*{col}*\n"
            for card in board[col]:
                card_id = card['id']
                title = card['title']
                text += f"`{card_id}`: {title}\n"
                # Action buttons: Move (to other columns) and Delete
                move_buttons = []
                for target in ("TODO", "IN_PROGRESS", "DONE"):
                    if target != col:
                        move_buttons.append(InlineKeyboardButton(f"Move to {target}", callback_data=f"kanban_move_{card_id}_{target}"))
                delete_button = InlineKeyboardButton("Delete", callback_data=f"kanban_del_{card_id}")
                row = move_buttons + [delete_button]
                keyboard.append(row)
            if not board[col]:
                text += "_없음_\n"
            text += "\n"
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

    if subcmd == "delete":
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ 사용법: `/kanban delete <id>`", parse_mode='Markdown')
            return
        try:
            card_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ 카드 ID는 숫자여야 합니다.", parse_mode='Markdown')
            return
        success = db.delete_card(card_id)
        if success:
            await update.message.reply_text(f"🗑️ 카드 `{card_id}` 를 삭제했습니다.", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ 카드 `{card_id}` 를 찾을 수 없었습니다.", parse_mode='Markdown')
        return

    await update.message.reply_text("⚠️ 알 수 없는 subcommand. 사용법: `/kanban [list|add|move|delete] ...`", parse_mode='Markdown')


# ======================================================================
# [8] cmd_audit — 인프라 감사 (포트/SSH/키)
# ======================================================================
async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/audit — 시스템 감사: 포트 상태, SSH 키, 환경 변수"""
    if not await check_user(update):
        return

    msg = await update.message.reply_text("🔍 시스템 감사 실행 중...")

    try:
        report = _audit_engine.infra_audit()

        lines = ["🏛️ **인프라 감사 리포트**\n"]

        # 포트 상태
        lines.append("🔌 **포트 상태:**")
        for name, status in report.get("ports", {}).items():
            lines.append(f"  {status} {name}")
        lines.append("")

        # SSH 키
        keys = report.get("ssh_keys", [])
        lines.append(f"🔑 **SSH 키 ({len(keys)}개):**")
        for k in keys[:5]:
            lines.append(f"  • `{k['file']}` ({k['type']})")
        if len(keys) > 5:
            lines.append(f"  ... 외 {len(keys)-5}개")
        lines.append("")

        # 환경 변수
        lines.append("🔐 **환경 변수:**")
        for var, status in report.get("env_keys", {}).items():
            lines.append(f"  {status} {var}")
        lines.append("")

        # 방화벽
        fw = report.get("firewall", "❓ 확인 불가")
        lines.append(f"🛡️ **방화벽:** {fw}")

        await msg.edit_text("\n".join(lines), parse_mode='Markdown')
        _audit_engine.log_audit("cmd_audit", "user_request", {"status": "completed"})
    except Exception as e:
        logger.error(f'cmd_audit error: {e}')
        await msg.edit_text(f"❌ 감사 실행 중 오류: {e}")


# ======================================================================
# [9] cmd_secreview — 보안 코드 리뷰 (git diff → DeepSeek 분석)
# ======================================================================
async def cmd_secreview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/secreview [경로] — 보안 코드 리뷰 (git diff 분석, Phase1→2→3, 80% 신뢰도 이상만 보고)"""
    if not await check_user(update):
        return

    target_path = ' '.join(context.args) if context.args else str(BASE_DIR)

    msg = await update.message.reply_text(
        f"🔐 보안 코드 리뷰 시작...\n📂 대상: `{target_path}`\n⏳ git diff 수집 중",
        parse_mode='Markdown'
    )

    try:
        # Phase 1: git diff 수집
        diff_cmd = ['git', '-C', str(BASE_DIR), 'diff']
        result = subprocess.run(diff_cmd, capture_output=True, text=True, timeout=10)
        git_diff = result.stdout if result.returncode == 0 else "⚠️ git diff 수집 실패"
        if not git_diff.strip() or git_diff.startswith("⚠️"):
            await msg.edit_text("📭 변경된 파일이 없습니다. 리뷰할 내용이 없습니다.")
            return

        # Phase 2: DeepSeek/CoVe 보안 분석
        await msg.edit_text("🔍 AI 보안 분석 진행 중... (Phase 2/3)")

        analysis_prompt = (
            f"다음 git diff를 보안 관점에서 분석하세요. Phase 1→2→3 단계로 진행:\n"
            f"Phase 1: 식별된 보안 이슈 목록화\n"
            f"Phase 2: 각 이슈의 심각도 분류 (CRITICAL/HIGH/MEDIUM/LOW)\n"
            f"Phase 3: 신뢰도 80% 이상인 항목만 최종 보고\n\n"
            f"분석할 git diff:\n```diff\n{git_diff[:4000]}\n```\n\n"
            f"보고 형식:\n"
            f"## 🔐 보안 코드 리뷰 결과\n"
            f"### 위험도별 요약\n"
            f"- 🔴 CRITICAL: N건\n"
            f"- 🟠 HIGH: N건\n"
            f"- 🟡 MEDIUM: N건\n"
            f"- 🔵 LOW: N건\n\n"
            f"### 상세 분석\n"
            f"(신뢰도 80%+ 항목만)\n"
            f"- 🔴 [CRITICAL][XX%] 제목\n"
            f"  - 위치: 파일명:라인\n"
            f"  - 설명: ...\n"
            f"  - 권장 조치: ..."
        )

        verified, _ = await cove_engine_instance.process_query(analysis_prompt, mode='balanced')
        _audit_engine.log_audit("secreview", target_path, {"status": "completed"})

        reply = f"🔐 **보안 코드 리뷰 완료**\n📂 대상: `{target_path}`\n\n{verified}"
        if len(reply) > 4000:
            reply = reply[:4000] + "\n\n... (결과가 깁니다. 일부만 표시)"

        await msg.edit_text(reply, parse_mode='Markdown')

    except Exception as e:
        logger.error(f'cmd_secreview error: {e}')
        await msg.edit_text(f"❌ 보안 리뷰 중 오류: {e}")


# ======================================================================
# handle_button_callback
# ======================================================================
async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """인라인 승인 버튼 클릭 처리"""
    query = update.callback_query
    await query.answer()

    data = query.data

    # === Harness 콜백 위임 ===
    if data.startswith('harness_'):
        try:
            from hermes_harness import _handle_harness_callback
            await _handle_harness_callback(update, context)
        except Exception as e:
            await query.edit_message_text(
                f'❌ Harness 콜백 오류: {e}'
            )
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
            await query.edit_message_text(result, parse_mode='Markdown')
        except Exception as e:
            logger.error(f'Purge error: {e}')
            await query.edit_message_text(f'❌ 캐시 정리 중 오류: {e}')
        return

    # === Kanban 콜백 처리 ===
    db = KanbanDB()
    if data.startswith('kanban_move_'):
        # format: kanban_move_<id>_<target>
        parts = data.split('_')
        if len(parts) >= 4:
            card_id = int(parts[2])
            target_col = parts[3]
            try:
                success = db.move_card(card_id, target_col)
                if success:
                    await query.edit_message_text(f'✅ 카드 `{card_id}` 를 **{target_col}** 로 이동했습니다.', parse_mode='Markdown')
                else:
                    await query.edit_message_text(f'❌ 카드 `{card_id}` 를 찾을 수 없거나 이동에 실패했습니다.', parse_mode='Markdown')
            except Exception as e:
                await query.edit_message_text(f'❌ 이동 중 오류: {e}', parse_mode='Markdown')
        return
    if data.startswith('kanban_del_'):
        # format: kanban_del_<id>
        parts = data.split('_')
        if len(parts) >= 3:
            card_id = int(parts[2])
            try:
                success = db.delete_card(card_id)
                if success:
                    await query.edit_message_text(f'🗑️ 카드 `{card_id}` 를 삭제했습니다.', parse_mode='Markdown')
                else:
                    await query.edit_message_text(f'❌ 카드 `{card_id}` 를 찾을 수 없습니다.', parse_mode='Markdown')
            except Exception as e:
                await query.edit_message_text(f'❌ 삭제 중 오류: {e}', parse_mode='Markdown')
        return

    # === 메모리 콜백 위임 ===
    if data.startswith('mem_'):
        try:
            from hermes_memory_patch import handle_memory_callback
            await handle_memory_callback(update, context)
        except Exception as e:
            logger.error(f'메모리 콜백 처리 실패: {e}')
            await query.edit_message_text(
                f'❌ 메모리 콜백 오류: {e}'
            )
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
                # SIGTERM으로 안 죽었으면 SIGKILL
                try:
                    os.kill(pid_int, 0)  # 프로세스 존재 확인
                    os.kill(pid_int, signal.SIGKILL)
                    res = f'💥 `{pid}` — SIGKILL 로 강제 종료했습니다'
                except OSError:
                    res = f'✅ `{pid}` — 정상 종료 완료'
            except OSError:
                res = f'✅ `{pid}` — 정상 종료 완료'

            # 원본 메시지 텍스트 유지
            text = query.message.text or ''
            await query.edit_message_text(
                text + '\n\n======================\n' + res,
                parse_mode='Markdown'
            )
        except Exception as e:
            text = query.message.text or ''
            await query.edit_message_text(
                text + '\n\n======================\n❌ 종료 실패: ' + str(e),
                parse_mode='Markdown'
            )
        return

    # === Dreaming 승인 ===
    if data == 'confirm_dreaming':
        await query.edit_message_text(
            query.message.text + '\n\n✅ Dreaming 실행 승인됨',
            parse_mode='Markdown'
        )
        await cmd_dreaming(update, context)
        return

    # === Ingest 승인 ===
    if data == 'confirm_ingest':
        await query.edit_message_text(
            query.message.text + '\n\n✅ Ingest 실행 승인됨',
            parse_mode='Markdown'
        )
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
                parse_mode="Markdown"
            )
        except Exception:
            # fallback: 새 메시지 전송
            await update.effective_chat.send_message(
                f"✅ **{MODE_LABELS.get(mode, mode)}**(으)로 모드 전환 완료!",
                parse_mode="Markdown"
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

        # 작업 완료 후 PENDING_TASKS에서 제거
        PENDING_TASKS.pop(data, None)

        # 원본 메시지 텍스트 확인
        original_text = query.message.text or query.message.caption or ''
        await query.edit_message_text(
            original_text + '\n\n' + res_text,
            parse_mode='Markdown'
        )
    else:
        # 만료된 작업
        await query.edit_message_text(
            (query.message.text or '') + '\n\n⚠️ 만료되었거나 이미 처리된 작업입니다.',
            parse_mode='Markdown'
        )


# ======================================================================
# _cmd_topmem  —  메모리 현황 확인 + 캐시 정리 버튼
# ======================================================================
async def _cmd_topmem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🧠 메모리 버튼: 현황 표시 + 캐시 정리(sudo purge) 원클릭"""
    try:
        mem_info = await _get_mem_info()
        report = (
            f'**💾 Mac Studio 메모리 현황**\n'
            f'총 용량: `{mem_info["total_gb"]:.1f} GB`\n'
            f'활성 사용: `{mem_info["used_gb"]:.1f} GB ({mem_info["used_pct"]:.1f}%)`\n'
            f'파일 캐시: `{mem_info["cached_gb"]:.1f} GB` ← 정리 대상\n'
            f'진짜 여유: `{mem_info["free_gb"]:.1f} GB`\n\n'
            f'🧹 **캐시 정리**를 누르면 파일 캐시 `{mem_info["cached_gb"]:.1f} GB`가\n'
            f'즉시 반환됩니다. 실행 중인 앱·파일은 전혀 영향 없습니다.'
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton('🧹 캐시 정리 실행 (sudo purge)', callback_data='mem_purge_confirm')
        ]])
        await update.message.reply_text(report, reply_markup=keyboard, parse_mode='Markdown')

    except Exception as e:
        logger.error(f'Memory query error: {e}')
        await update.message.reply_text(f'❌ 메모리 조회 중 오류: {e}')


async def _get_mem_info() -> dict:
    """vm_stat 파싱 → 메모리 통계 dict 반환"""
    total_proc = await asyncio.create_subprocess_shell(
        'sysctl hw.memsize', stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    total_out, _ = await total_proc.communicate()
    total_bytes = int(total_out.decode().strip().split(':')[1].strip())
    total_gb = total_bytes / (1024 ** 3)

    vm_proc = await asyncio.create_subprocess_shell(
        'vm_stat', stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    vm_out, _ = await vm_proc.communicate()
    vm_lines = vm_out.decode().strip().split('\n')

    page_size = 16384
    stats = {'free': 0, 'active': 0, 'wired': 0, 'cached': 0}
    for line in vm_lines:
        if 'Pages free' in line:
            stats['free'] = int(line.split(':')[1].strip().rstrip('.'))
        elif 'Pages active' in line:
            stats['active'] = int(line.split(':')[1].strip().rstrip('.'))
        elif 'Pages wired down' in line:
            stats['wired'] = int(line.split(':')[1].strip().rstrip('.'))
        elif 'File-backed pages' in line:
            stats['cached'] = int(line.split(':')[1].strip().rstrip('.'))

    to_gb = lambda pages: (pages * page_size) / (1024 ** 3)
    used_gb = to_gb(stats['active'] + stats['wired'])
    return {
        'total_gb': total_gb,
        'used_gb': used_gb,
        'used_pct': (used_gb / total_gb * 100) if total_gb > 0 else 0,
        'cached_gb': to_gb(stats['cached']),
        'free_gb': to_gb(stats['free']),
    }


# ======================================================================
# cmd_paper — 논문 작업 명령어 (humanize / draft / review)
# ======================================================================
async def cmd_paper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /paper humanize [텍스트] — 논문 초안/법률 문건을 학술 문체로 변환
    /paper draft [주제]  — 논문 초안 생성 (추후)
    /paper review [문서] — 논문 검토 (추후)
    """
    from telegram.constants import ParseMode
    args = context.args
    if not args:
        await update.message.reply_text(
            "📄 **/paper 사용법**\n\n"
            "• `/paper humanize [텍스트]` — 법률/학술 문체 변환\n"
            "• `/paper draft [주제]` — 논문 초안 생성 (준비 중)\n"
            "• `/paper review [문서]` — 논문 검토 (준비 중)",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    subcmd = args[0].lower()

    # ── /paper humanize ────────────────────────────────────────────
    if subcmd == 'humanize':
        text = ' '.join(args[1:]) if len(args) > 1 else ''
        if not text:
            await update.message.reply_text(
                "✍️ **/paper humanize [텍스트]**\n\n"
                "변환할 텍스트를 입력해주세요.\n"
                "예: `/paper humanize 이 연구는 AI 알고리즘의 차별적 결과가 현행 평등권 법리로 규율 가능한지 검토한다.`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await update.message.reply_chat_action("typing")

        # LLM 호출: humanizer 스타일 프롬프트로 학술 문체 변환
        llm_prompt = (
            "You are an academic writing assistant specializing in Korean legal academia and AI research. "
            "Transform the following text into formal academic prose suitable for Korean law journals "
            "(서울대학교 법학, 법조, 저스티스, 법학연구 등). "
            "Requirements:\n"
            "- Use precise legal/academic terminology\n"
            "- Maintain rigorous argument structure\n"
            "- Remove colloquialisms and informal phrasing\n"
            "- Ensure logical flow with appropriate transitions\n"
            "- Keep the original meaning and factual claims intact\n"
            "- Output in the same language as the input\n"
            "- Follow Korean legal academic writing conventions\n"
            "- Use Korean legal terminology (판례 인용 시 대법원/헌법재판소 형식 준수)\n"
            "- Maintain honorific-neutral formal register (합쇼체 금지, 명사형 종결 선호)\n"
            "- Citation format: 각주 방식, 저자명(발행연도), 면수\n\n"
            f"Text to transform:\n{text}"
        )

        reply = await _call_llm(llm_prompt)

        await update.message.reply_text(
            f"✍️ **학술 문체 변환 완료**\n\n{reply}",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── /paper draft (준비 중) ─────────────────────────────────────
    if subcmd == 'draft':
        await update.message.reply_text(
            "📝 **/paper draft** — 논문 초안 생성 기능은 준비 중입니다.\n"
            "먼저 `/paper humanize`를 사용해보세요.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── /paper review (준비 중) ────────────────────────────────────
    if subcmd == 'review':
        await update.message.reply_text(
            "📋 **/paper review** — 논문 검토 기능은 준비 중입니다.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await update.message.reply_text(
        f"❌ 알 수 없는 하위 명령어: `{subcmd}`\n"
        "사용법: `/paper humanize`, `/paper draft`, `/paper review`",
        parse_mode=ParseMode.MARKDOWN
    )


async def _call_llm(prompt: str, provider: str = "deepseek") -> str:
    """LLM 호출 유틸리티 (hermes_handlers 내부용)"""
    import sys
    try:
        router = getattr(sys.modules.get('hybrid_router'), 'router', None)
        if router and hasattr(router, 'call'):
            result = await router.call(
                prompt=prompt,
                provider_type=provider,
                system_prompt="You are a helpful academic assistant."
            )
            if result and isinstance(result, dict):
                return result.get('content') or result.get('text', '')
    except Exception as e:
        logger.warning(f"[_call_llm] router 실패: {e}")

    # fallback: 직접 DeepSeek API 호출
    import aiohttp
    api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        # llama.cpp fallback
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "prompt": f"<|system|>You are a helpful academic assistant.</s>\n<|user|>{prompt}</s>\n<|assistant|>",
                    "n_predict": 1024,
                    "temperature": 0.7,
                }
                async with session.post("http://127.0.0.1:8080/completion", json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    data = await resp.json()
                    return data.get('content', '')
        except Exception as e2:
            return f"[LLM 호출 실패: {e2}]"

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "You are a helpful academic assistant."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 2048,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            async with session.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                data = await resp.json()
                choices = data.get('choices', [])
                if choices:
                    return choices[0].get('message', {}).get('content', '')
                return f"[API 응답 오류: {data}]"
    except Exception as e:
        return f"[DeepSeek API 호출 실패: {e}]"



# ======================================================================
# handle_text_message
# ======================================================================
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    메뉴 버튼 클릭 또는 일반 질문 매핑
    - 메뉴 버튼: 직접 처리
    - 일반 텍스트: harness_agent.handle_message 로 라우팅 (단일 봇 통합)
    """
    if not await check_user(update):
        return

    text = update.message.text.strip()
    # Kanban command handling
    if text.startswith('/kanban'):
        await cmd_kanban(update, context)
        return

    # === 메뉴 버튼 매핑 ===
    if text == '🌙 Dreaming':
        keyboard = [[
            InlineKeyboardButton('✅ 실행 승인', callback_data='confirm_dreaming')
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            '🌙 **Dreaming (지능형 기억/핫토픽 분배)** 작업을 시작하시겠습니까?\n'
            '이 작업은 시스템 자원을 사용하며 시간이 다소 소요될 수 있습니다.',
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    if text == '📥 Ingest':
        keyboard = [[
            InlineKeyboardButton('✅ 실행 승인', callback_data='confirm_ingest')
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            '📥 **Ingest (Clippings 위키 자동 이관)** 작업을 실행하시겠습니까?',
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    if text == '📁 최근 문서':
        await cmd_recent(update, context)
        return

    if text == 'ℹ️ 도움말':
        await cmd_help(update, context)
        return

    if text == '🛡️ 하네스':
        from hermes_harness import cmd_harness
        context.args = []  # 인자 초기화
        await cmd_harness(update, context)
        return

    if text == '📊 진단로그':
        from hermes_harness import cmd_hstatus
        await cmd_hstatus(update, context)
        return

    if text == '🧠 메모리':
        await _cmd_topmem(update, context)
        return

    if text == '✍️ 논문':
        context.args = []
        await cmd_paper(update, context)
        return

    # === 일반 텍스트 → harness_agent 라우팅 ===
    try:
        import importlib
        ha = importlib.import_module('harness_agent')
        await ha.handle_message(update, context)
    except Exception as e:
        logger.error(f'[harness_agent] 라우팅 실패: {e}')
        # fallback: cmd_ask로 처리
        context.args = text.split()
        await cmd_ask(update, context)
