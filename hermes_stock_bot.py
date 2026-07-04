"""
hermes_stock_bot.py — @Ulsan_Antigravity_bot 전담 봇 (Hermes2)
역할: 주식 명령어(/scan /market /stock /watchlist /mjstock) + AI 대화(GPT OSS 120B)
토큰: TELEGRAM_BOT_TOKEN (screener/.env → ~/.hermes/.env)
AI:   NVIDIA openai/gpt-oss-120b (NVIDIA_GPT_API_KEY)
"""
import os, sys, asyncio, logging
from pathlib import Path
from dotenv import load_dotenv

# .env 로드 순서: screener/.env → ~/.hermes/.env
load_dotenv(Path('/Users/bluesea/Applications/Mjstock/screener/.env'))
load_dotenv(Path.home() / '.hermes' / '.env')

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
ALLOWED_ID = int((os.environ.get('TELEGRAM_ALLOWED_USERS', '5365732604') or '5365732604').split(',')[0])
NVIDIA_KEY = os.environ.get('NVIDIA_GPT_API_KEY', '') or os.environ.get('NVIDIA_API_KEY', '')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('HermesStockBot')

if not TOKEN:
    logger.error('TELEGRAM_BOT_TOKEN 없음 — screener/.env 확인')
    sys.exit(1)

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

sys.path.insert(0, str(Path(__file__).parent))

# ── AI 대화 (NVIDIA GPT OSS 120B) ─────────────────────────
import openai

_nvidia_client = None
if NVIDIA_KEY:
    _nvidia_client = openai.OpenAI(
        base_url='https://integrate.api.nvidia.com/v1',
        api_key=NVIDIA_KEY,
        timeout=120.0,
    )

# chat_id -> 최근 대화 이력 (최대 10턴)
_history: dict[int, list[dict]] = {}
_MAX_HISTORY = 20  # user+assistant 합산 메시지 수

_SYSTEM_PROMPT = (
    '당신은 MJ님의 AI 어시스턴트 Hermes2입니다. '
    '한국어로 간결하고 정확하게 답합니다. '
    '주식 스캔/분석이 필요하면 /help 명령어를 안내하세요.'
)


def _call_gpt_oss(chat_id: int, prompt: str) -> str:
    """NVIDIA gpt-oss-120b 동기 호출 (executor에서 실행)"""
    if not _nvidia_client:
        return '⚠️ NVIDIA_GPT_API_KEY가 설정되지 않았습니다.'
    hist = _history.get(chat_id, [])
    messages = [{'role': 'system', 'content': _SYSTEM_PROMPT}] + hist + [
        {'role': 'user', 'content': prompt}
    ]
    try:
        resp = _nvidia_client.chat.completions.create(
            model='openai/gpt-oss-120b',
            messages=messages,
            temperature=0.7,
            max_tokens=2048,  # 추론 토큰 여유 확보 (적으면 content 빈값)
        )
        msg = resp.choices[0].message
        answer = (msg.content or '').strip()
        if not answer:  # 추론에 토큰 다 쓰면 reasoning 폴백
            answer = (getattr(msg, 'reasoning_content', '') or '').strip()
        if not answer:
            return '⚠️ 응답이 비어 있습니다. 다시 시도해주세요.'
        # 이력 갱신
        hist = hist + [
            {'role': 'user', 'content': prompt},
            {'role': 'assistant', 'content': answer},
        ]
        _history[chat_id] = hist[-_MAX_HISTORY:]
        return answer
    except Exception as e:
        logger.error(f'GPT OSS 호출 실패: {e}')
        return f'⚠️ AI 오류: {e}'


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """명령어가 아닌 일반 텍스트 → AI 대화"""
    if not await check_user(update):
        return
    if not update.message or not update.message.text:
        return
    prompt = update.message.text.strip()
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(None, _call_gpt_oss, chat_id, prompt)
    # 텔레그램 메시지 길이 제한 (4096)
    await update.message.reply_text(answer[:4000])


async def check_user(update: Update) -> bool:
    if update.effective_user and update.effective_user.id != ALLOWED_ID:
        await update.message.reply_text('❌ 접근 권한 없음')
        return False
    return True


HELP_TEXT = (
    '📈 <b>MJstock 주식 봇 (@Ulsan_Antigravity_bot)</b>\n\n'
    '<b>스캔 명령어:</b>\n'
    '• <code>/scan</code> — 전체 종목 스캔 (US+KR)\n'
    '• <code>/market</code> — 시장 필터 스캔\n\n'
    '<b>종목 분석:</b>\n'
    '• <code>/stock TICKER</code> — 개별 종목 분석\n'
    '• <code>/mjstock TICKER</code> — 상세 퀀트 분석\n\n'
    '<b>관심종목:</b>\n'
    '• <code>/watchlist</code> — 관심종목 조회\n\n'
    '<b>코인 분석 (빗썸):</b>\n'
    '• <code>/coin all</code> — 전체 코인 6개 검색기 스캔\n'
    '• <code>/coin ETH</code> — 개별 코인 분석 + 차트\n\n'
    '📬 정기 스캔 결과는 자동으로 이 채널로 전송됩니다.'
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode='HTML')


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode='HTML')


def _load_stock_handlers():
    from handlers._stock import (
        cmd_stock, cmd_scan, cmd_market, cmd_watchlist, cmd_mjstock, cmd_coin,
        callback_mjstock, callback_mjstock_results, callback_coin,
    )
    return cmd_stock, cmd_scan, cmd_market, cmd_watchlist, cmd_mjstock, cmd_coin, callback_mjstock, callback_mjstock_results, callback_coin


def main():
    try:
        cmd_stock, cmd_scan, cmd_market, cmd_watchlist, cmd_mjstock, cmd_coin, callback_mjstock, callback_mjstock_results, callback_coin = _load_stock_handlers()
    except Exception as e:
        logger.error(f'주식 핸들러 로드 실패: {e}')
        sys.exit(1)

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start',     cmd_start))
    app.add_handler(CommandHandler('help',      cmd_help))
    app.add_handler(CommandHandler('stock',     cmd_stock))
    app.add_handler(CommandHandler('scan',      cmd_scan))
    app.add_handler(CommandHandler('market',    cmd_market))
    app.add_handler(CommandHandler('watchlist', cmd_watchlist))
    app.add_handler(CommandHandler('mjstock',   cmd_mjstock))
    app.add_handler(CommandHandler('coin',      cmd_coin))
    # 인라인 버튼 콜백: 아침/장중 스캔 [결과 보기] 버튼 (더 구체적 패턴 — 반드시 mjstock[_:] 앞에)
    app.add_handler(CallbackQueryHandler(callback_mjstock_results, pattern=r'^mjstock_results__'))
    # 인라인 버튼 콜백: mjstock 분석 결과 버튼
    app.add_handler(CallbackQueryHandler(callback_mjstock,         pattern=r'^mjstock[_:]'))
    # 인라인 버튼 콜백: coin 분석 버튼 (coin_all: / coin_chart:)
    app.add_handler(CallbackQueryHandler(callback_coin,            pattern=r'^coin_'))
    # AI 대화: 명령어 아닌 일반 텍스트 (반드시 마지막 등록)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info(f'🧠 AI 대화: {"GPT OSS 120B 활성" if _nvidia_client else "비활성 (키 없음)"}')

    logger.info(f'✅ HermesStockBot 시작 (ALLOWED_ID={ALLOWED_ID})')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
