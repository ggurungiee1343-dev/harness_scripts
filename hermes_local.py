"""
hermes_local.py — 헤르메스 V2.5 자율 관제 봇 메인 엔트리 (v8.6 가동 아키텍처)
==========================================================================
"""

import os
import sys
import shutil
import unicodedata
import logging
import warnings
import fcntl
import atexit
import importlib
import subprocess

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)
logging.getLogger("asyncio").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
from pathlib import Path

_SCRIPTS_DIR = '/Users/bluesea/Applications/Mjauto/Scripts'
_MODULES_DIR = '/Users/bluesea/Applications/Mjauto/Scripts/modules'
_HERMES_DIR = '/Users/bluesea/Applications/Mjauto/Scripts'

for _p in (_SCRIPTS_DIR, _MODULES_DIR, _HERMES_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import Conflict, TelegramError

from modules.core_reducer import AgentContext, SourceChannel, HermesCoreReducer, get_reducer

_module_cache = {}

async def _reducer_llm(prompt: str) -> str:
    """DecisionAgent용 실시간 LLM 라우터 — 현재 모드(Gemma4/DeepSeek) 반영"""
    from hybrid_router import router as _hr_router
    res, name = _hr_router.send_completion(prompt)
    return res

_core_reducer = HermesCoreReducer(llm=_reducer_llm)


def _load_module(name: str):
    if name not in _module_cache:
        _module_cache[name] = importlib.import_module(name)
    return _module_cache[name]


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('HermesOrchestrator')


async def global_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        logger.warning(f'Telegram Conflict error: {context.error}')
    elif isinstance(context.error, TelegramError):
        logger.warning(f'Telegram error: {context.error}')
    else:
        logger.error(f'Unexpected error: {context.error}')


try:
    from agent_harness import AgentHarness
    from harness_config import HarnessConfig as HCFG
    HARNESS_ENABLED = True
except ImportError:
    HARNESS_ENABLED = False
    logger.warning('[하네스] agent_harness.py 또는 harness_config.py 를 찾을 수 없습니다. /harness 명령어가 비활성화됩니다.')

try:
    from history_manager import HistoryManager
    from wiki_manager import WikiManager
except ImportError:
    history_mgr = None
    wiki_mgr = None


def load_custom_env(env_path: str = '/Users/bluesea/.hermes/.env'):
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"')
                os.environ[k] = v


load_custom_env()

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ALLOWED_ID_STR = os.environ.get('TELEGRAM_ALLOWED_USERS', '5365732604')
ALLOWED_ID = int(ALLOWED_ID_STR.split(',')[0])

BASE_DIR = Path('/Users/bluesea/Applications/Mjobsidian').resolve()

ALLOWED_BASES = [
    Path('/Users/bluesea/Applications').resolve(),
    Path('/Users/bluesea/hermes').resolve(),
    Path('/Users/bluesea/.hermes').resolve(),
    Path('/Users/bluesea/Applications/venu').resolve(),
]

HELP_TEXT = """🤖 **헤르메스 V2.5 명령어 가이드 (완전판)**

🔹 **인텔리전스 & 팩트체크**
• `/ask [질문]` — 기본 답변 및 위키 기반 Q&A
• `/cove [질문]` — Gemma4 CoVe 4단계 팩트체크 (devil 모드 지원)
• `/web [URL] [질문]` — 웹 주소 요약/분석
• `/search [검색어]` — 웹 브라우징 요약
• `/readweb [URL]` — 웹페이지 요약
• `/searchpaper [검색어]` — 논문 검색
• `/paper [명령]` — 논문 관련 기능 (humanize/draft/review)
• `/research [유형]` — 심층 리서치 (local/web/deep/stats/xref/classify/timeline)
• `/reduce [질의]` — v9.0 core_reducer 통합 테스트

🤖 **에이전트 & 자율 작업**
• `/orchestrate [목표]` — 에이전트 병렬 다중 실행
• `/exec [명령어]` — AI 자율 Bash 실행
• `/delegate [작업]` — Task 분리 및 위임
• `/handoff` — 에이전트 핸드오프
• `/caveman` — 원시 모드(단순 응답) 토글

🔍 **Vault & 지식 정원**
• `/ingest` — 신규 파일 이관 및 정리
• `/clip [텍스트]` — 클립보드 즉시 저장
• `/vault [명령]` — 보관소 진단 (check/duplicates 등)
• `/grill [문서] [질문]` — Vault 문서 심층 Q&A
• `/tag [명령]` — 태그 관리 (pending/approve/reject)
• `/fs` — 파일 시스템 도구 모음

🧠 **메모리 & 목표 관리**
• `/memory` — 3계층 Bio-Memory(L1/L2/L3) 상태 모니터링
• `/memory_search [검색어]` — 메모리 통합 검색
• `/memory_dream` — Dreaming 강제 실행
• `/memory_audit` — 메모리 종합 감사
• `/dreaming` — 대화/작업 저널 분배 자동화
• `/goal [목표]` — 장기 목표 설정 및 관리

📊 **모니터링 & 시스템**
• `/recent` — 최근 수정 문서 출력
• `/status` — 시스템 핫토픽 점검
• `/kanban` — 칸반 보드 상태 및 관리
• `/audit` — 시스템/인프라 종합 감사
• `/secreview` — 보안 코드 리뷰
• `/restart_bot` — 봇 프로세스 재시작
• `/model` — 모델 설정 상태
• `/profile [모드]` — 작업 모드 변경

🛡️ **하네스 컨트롤**
• `/harness` — ACID 하네스 상태 제어
• `/hdod` — 하네스 DoD 진단
• `/hstatus` — 시스템 진단 리포트
• `/hrollback` — 의사결정 롤백 실행
"""

PENDING_TASKS = {}
HARNESS_SESSIONS = {}

history_mgr = HistoryManager('/Users/bluesea/Applications/Mjauto/Scripts/harness_memory.json') if 'HistoryManager' in globals() else None
wiki_mgr = WikiManager(vault_path=str(BASE_DIR)) if 'WikiManager' in globals() else None


def init_dir():
    for d in ['inbox', 'working', 'outbox']:
        Path(_HERMES_DIR, d).mkdir(parents=True, exist_ok=True)


async def check_user(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if uid != ALLOWED_ID:
        await update.message.reply_text('⛔ 허용되지 않은 사용자입니다.')
        return False
    return True


def secure_path(user_path_str: str) -> Path:
    normalized_str = unicodedata.normalize('NFC', str(user_path_str))
    path_obj = Path(normalized_str)
    if path_obj.is_absolute():
        target_path = path_obj.resolve()
    else:
        target_path = (BASE_DIR / path_obj).resolve()
    for base in ALLOWED_BASES:
        try:
            target_path.relative_to(base)
            return target_path
        except ValueError:
            continue
    raise PermissionError(f"Access Denied.")


def _make_keyboard():
    keyboard = [
        [KeyboardButton('🌙 Dreaming'), KeyboardButton('📁 최근 문서')],
        [KeyboardButton('📥 Ingest'), KeyboardButton('🔍 보관함 진단')],
        [KeyboardButton('✍️ 논문'), KeyboardButton('🧠 메모리')],
        [KeyboardButton('🔄 모드 전환')],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def _run_end_hook():
    import logging as _log
    hook_path = os.path.expanduser('~/.hermes/hooks/end.sh')
    if not os.path.exists(hook_path): return
    try:
        result = subprocess.run(['bash', hook_path], capture_output=True, text=True, timeout=30)
    except Exception: pass


def main():
    LOCK_FILE = Path.home() / '.hermes' / 'hermes_local.lock'
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fp = open(LOCK_FILE, 'w')

    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('⚠️ 다른 hermes_local 인스턴스가 이미 실행 중입니다. 종료합니다.')
        sys.exit(0)

    atexit.register(lambda fp=lock_fp: (fcntl.flock(fp, fcntl.LOCK_UN), fp.close()))
    atexit.register(_run_end_hook)

    if not TOKEN: sys.exit(1)

    init_dir()
    app = Application.builder().token(TOKEN).build()

    async def post_init(application):
        try:
            await application.bot.send_message(
                chat_id=ALLOWED_ID,
                text='🔄 **Hermes3 v8.6 차세대 아키텍처 관제 가동 완료**',
                reply_markup=_make_keyboard(),
                parse_mode='Markdown'
            )
        except Exception: pass

        # v9.1 Cache Cleanup Scheduler
        async def _cache_cleanup_loop():
            import asyncio
            try:
                from modules.core_reducer import AgentContext, SourceChannel, HermesCoreReducer, get_reducer
                reducer = get_reducer()
                while True:
                    await asyncio.sleep(3600) # 1 hour
                    await reducer.cleanup_expired_cache()
            except Exception as e:
                logger.error(f"[v9.1 Cache] Cleanup loop failed: {e}")
        
        import asyncio
        asyncio.create_task(_cache_cleanup_loop())

    app.post_init = post_init

    def _load_handler(name: str):
        mod = _load_module('handlers')
        return getattr(mod, name)

    cmd_start = lambda *a, **kw: _load_handler('cmd_start')(*a, **kw)

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_user(update): return
        await update.message.reply_text(HELP_TEXT, parse_mode='Markdown')

    cmd_help = _load_module('handlers').cmd_help
    cmd_ask = lambda *a, **kw: _load_handler('cmd_ask')(*a, **kw)
    cmd_cove = lambda *a, **kw: _load_handler('cmd_cove')(*a, **kw)
    cmd_web = lambda *a, **kw: _load_handler('cmd_web')(*a, **kw)
    cmd_ingest = lambda *a, **kw: _load_handler('cmd_ingest')(*a, **kw)
    
    # 🏰 [Hermes3 v8.9] SKILLOPT & FluxMem 아키텍처 통합 컴포넌트 부트스트랩 주입
    try:
        from modules.dreaming_v2 import DreamingV2
        global dreaming_engine
        dreaming_engine = DreamingV2()
        logger.info("✅ [v8.9 아키텍처] Dreaming v2.0(SKILLOPT+FluxMem 융합형) 이벤트 기반 버스 바인딩 완결.")
    except Exception as e:
        logger.error(f"⚠️ [v8.9 아키텍처] Dreaming 엔진 이식 실패: {e}")

    async def cmd_dreaming_v89(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_user(update): return
        await update.message.reply_text("🌙 [v8.9 FluxMem] PEMS 자가진단 및 지식 증류 연산 가동...")
        try:
            count = await dreaming_engine.deep_distill()
            if count > 0:
                await update.message.reply_text(f"✅ 진화 성공: 총 {count}개의 원시 이력이 고농축 마크다운 지식 회로로 전환되었습니다.")
            else:
                await update.message.reply_text("💤 시스템 알림: 현재 지식 성숙도가 최적의 상태(수렴)이므로 불필요한 LLM 호출을 생략했습니다.")
        except Exception as e:
            await update.message.reply_text(f"❌ 진화 실패: {e}")

    # ===== v9.0 핸들러 등록 =====
    cmd_exec = lambda *a, **kw: _load_handler('cmd_exec')(*a, **kw)
    cmd_clip = lambda *a, **kw: _load_handler('cmd_clip')(*a, **kw)
    cmd_recent = lambda *a, **kw: _load_handler('cmd_recent')(*a, **kw)
    cmd_status = lambda *a, **kw: _load_handler('cmd_status')(*a, **kw)
    cmd_goal = lambda *a, **kw: _load_handler('cmd_goal')(*a, **kw)
    cmd_kanban = lambda *a, **kw: _load_handler('cmd_kanban')(*a, **kw)
    cmd_delegate = lambda *a, **kw: _load_handler('cmd_delegate')(*a, **kw)
    cmd_audit = lambda *a, **kw: _load_handler('cmd_audit')(*a, **kw)
    cmd_secreview = lambda *a, **kw: _load_handler('cmd_secreview')(*a, **kw)
    cmd_paper = lambda *a, **kw: _load_handler('cmd_paper')(*a, **kw)
    cmd_research = lambda *a, **kw: _load_handler('cmd_research')(*a, **kw)
    cmd_orchestrate = lambda *a, **kw: _load_handler('cmd_orchestrate')(*a, **kw)
    cmd_vault = lambda *a, **kw: _load_handler('cmd_vault')(*a, **kw)
    cmd_grill = lambda *a, **kw: _load_handler('cmd_grill')(*a, **kw)
    cmd_restart_bot = lambda *a, **kw: _load_handler('cmd_restart_bot')(*a, **kw)
    cmd_memory = lambda *a, **kw: _load_handler('cmd_memory')(*a, **kw)
    try:
        from hermes_memory_patch import cmd_memory_search, cmd_memory_audit
    except Exception:
        cmd_memory_search = lambda *a, **kw: _load_handler('cmd_memory')(*a, **kw)
        cmd_memory_audit = cmd_memory_search
    try:
        _test_mod = importlib.import_module('handlers')
        assert hasattr(_test_mod, 'cmd_tag')
    except Exception: pass

    cmd_tag = lambda *a, **kw: _load_handler('cmd_tag')(*a, **kw)

    # 🏰 [Hermes3 v8.6] 부팅 시 이벤트 버스 중재 구독 인터셉터 강제 연결
    try:
        from modules.tag_linker import TagLinker
        def _v86_bridge_cascade_listener(file_path, tag):
            try:
                from modules.ontology_graph import OntologyGraph
                from modules.cascade_engine import CascadeEngine
                cascade = CascadeEngine(OntologyGraph())
                cascade.trigger_tag_change(file_path, tag, action="approve")
                logger.info(f"⚡ [v8.6 Pub-Sub Bus] 태그 승인 감지 -> 온톨로지 캐스캐이드 연쇄 갱신 전파 성공.")
            except Exception as e:
                logger.error(f"⚠️ [v8.6 Pub-Sub Bus] 연쇄 전파 실패: {e}")

        TagLinker.subscribe("tag_approved", _v86_bridge_cascade_listener)
        logger.info("✅ [v8.6 아키텍처] DB 레이어와 지식 캐스케이드 간의 Pub-Sub 동적 버스 결합 성공.")
    except Exception: pass

    try:
        from handlers._system import cmd_model as _cmd_model
        cmd_model = _cmd_model
    except Exception: cmd_model = lambda *a, **kw: _load_handler('cmd_model')(*a, **kw)

    try:
        from handlers._system import cmd_caveman as _cmd_caveman
        cmd_caveman = _cmd_caveman
    except Exception: cmd_caveman = lambda *a, **kw: _load_handler('cmd_caveman')(*a, **kw)

    try:
        from handlers._system import cmd_handoff as _cmd_handoff
        cmd_handoff = _cmd_handoff
    except Exception: cmd_handoff = lambda *a, **kw: _load_handler('cmd_handoff')(*a, **kw)

    try:
        from handlers._system import cmd_fs as _cmd_fs
        cmd_fs = _cmd_fs
    except Exception: cmd_fs = lambda *a, **kw: _load_handler('cmd_fs')(*a, **kw)

    try:
        from handlers._meta import cmd_claude_brief as _cmd_claude_brief
        cmd_claude_brief = _cmd_claude_brief
    except Exception: cmd_claude_brief = lambda *a, **kw: _load_handler('cmd_claude_brief')(*a, **kw)

    try:
        from handlers._ui import cmd_verify_harness as _cmd_verify_harness
        cmd_verify_harness = _cmd_verify_harness
    except Exception: cmd_verify_harness = lambda *a, **kw: _load_handler('cmd_verify_harness')(*a, **kw)

    try:
        from handlers._callbacks import handle_retry_callback as _cmd_retry
        cmd_retry = _cmd_retry
    except Exception: cmd_retry = lambda *a, **kw: _load_handler('handle_retry_callback')(*a, **kw)

    async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await _load_handler('handle_button_callback')(update, context)

    handle_text_message = lambda *a, **kw: _load_handler('handle_text_message')(*a, **kw)

    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('menu', cmd_start))
    app.add_handler(CommandHandler('help', cmd_help))
    app.add_handler(CommandHandler('ask', cmd_ask))
    app.add_handler(CommandHandler('cove', cmd_cove))
    app.add_handler(CommandHandler('web', cmd_web))
    app.add_handler(CommandHandler('ingest', cmd_ingest))
    app.add_handler(CommandHandler('dreaming', cmd_dreaming_v89))
    app.add_handler(CommandHandler('memory_dream', cmd_dreaming_v89))
    app.add_handler(CommandHandler('exec', cmd_exec))
    app.add_handler(CommandHandler('clip', cmd_clip))
    app.add_handler(CommandHandler('recent', cmd_recent))
    app.add_handler(CommandHandler('status', cmd_status))
    app.add_handler(CommandHandler('goal', cmd_goal))
    app.add_handler(CommandHandler('delegate', cmd_delegate))
    app.add_handler(CommandHandler('kanban', cmd_kanban))
    app.add_handler(CommandHandler('audit', cmd_audit))
    app.add_handler(CommandHandler('secreview', cmd_secreview))
    app.add_handler(CommandHandler('paper', cmd_paper))
    app.add_handler(CommandHandler('research', cmd_research))
    app.add_handler(CommandHandler('orchestrate', cmd_orchestrate))
    app.add_handler(CommandHandler('vault', cmd_vault))
    app.add_handler(CommandHandler('grill', cmd_grill))
    app.add_handler(CommandHandler('restart_bot', cmd_restart_bot))
    app.add_handler(CommandHandler('tag', cmd_tag))
    app.add_handler(CommandHandler('model', cmd_model))
    app.add_handler(CommandHandler('caveman', cmd_caveman))
    app.add_handler(CommandHandler('handoff', cmd_handoff))
    app.add_handler(CommandHandler('reduce', cmd_reduce))
    app.add_handler(CommandHandler('fs', cmd_fs))
    app.add_handler(CommandHandler('memory', cmd_memory))
    app.add_handler(CommandHandler('memory_search', cmd_memory_search))
    app.add_handler(CommandHandler('memory_audit', cmd_memory_audit))
    app.add_handler(CommandHandler('claude_brief', cmd_claude_brief))
    app.add_handler(CommandHandler('verify_harness', cmd_verify_harness))
    app.add_handler(CommandHandler('retry', cmd_retry))

    # ── 주식 분석 핸들러 (V_FINAL) ──────────────────────────
    try:
        from handlers._stock import (
            cmd_stock, cmd_scan, cmd_market, cmd_watchlist,
            cmd_positions, cmd_result, cmd_backtest, handle_stock_photo
        )
        app.add_handler(CommandHandler('stock',     cmd_stock))
        app.add_handler(CommandHandler('scan',      cmd_scan))
        app.add_handler(CommandHandler('market',    cmd_market))
        app.add_handler(CommandHandler('watchlist', cmd_watchlist))
        app.add_handler(CommandHandler('positions', cmd_positions))
        app.add_handler(CommandHandler('result',    cmd_result))
        app.add_handler(CommandHandler('backtest',  cmd_backtest))
        app.add_handler(MessageHandler(filters.PHOTO, handle_stock_photo))
        logger.info("✅ 주식 분석 핸들러 등록 완료 (V_FINAL + 피드백 루프)")
    except Exception as e:
        logger.warning(f"주식 핸들러 로드 실패: {e}")

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(CallbackQueryHandler(handle_button_callback))
    app.add_error_handler(global_error_handler)

    import asyncio as _asyncio_local
    async def _run_polling_managed():
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            while app.updater.running: await _asyncio_local.sleep(15)

    try: _asyncio_local.run(_run_polling_managed())
    except Exception: pass
    finally: sys.exit(0)


async def cmd_reduce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /reduce <질의> — v9.0 core_reducer 통합 테스트
    """
    
    if not await check_user(update):
        return
    
    # /reduce 다음의 모든 텍스트 추출
    query = update.message.text.replace("/reduce", "").strip()
    if not query:
        await update.message.reply_text("📝 사용법: /reduce <질의>")
        return
    
    try:
        ctx = AgentContext(
            user_query=query,
            source_channel=SourceChannel.TELEGRAM,
            user_id=str(update.effective_user.id),
            metadata={"chat_id": update.effective_chat.id}
        )
        
        processing_msg = await update.message.reply_text("🤔 처리 중...")
        result = await _core_reducer.reduce(ctx)
        await processing_msg.delete()
        
        if not result:
            await update.message.reply_text("🤷 /reduce 결과가 비어 있습니다. (핸들러가 응답을 생성하지 못했습니다)")
        else:
            await update.message.reply_text(result)
        
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {str(e)[:100]}")

if __name__ == '__main__':
    main()
