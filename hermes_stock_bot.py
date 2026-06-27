"""
hermes_stock_bot.py — @Ulsan_Antigravity_bot 전담 주식 봇
역할: /scan /market /stock /watchlist /mjstock 명령어 처리
토큰: HERMES2_BOT_TOKEN (screener/.env → ~/.hermes/.env)
"""
import os, sys, logging
from pathlib import Path
from dotenv import load_dotenv

# .env 로드 순서: screener/.env → ~/.hermes/.env
load_dotenv(Path('/Users/bluesea/Applications/Mjstock/screener/.env'))
load_dotenv(Path.home() / '.hermes' / '.env')

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
ALLOWED_ID = int((os.environ.get('TELEGRAM_ALLOWED_USERS', '5365732604') or '5365732604').split(',')[0])

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('HermesStockBot')

if not TOKEN:
    logger.error('TELEGRAM_BOT_TOKEN 없음 — screener/.env 확인')
    sys.exit(1)

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

sys.path.insert(0, str(Path(__file__).parent))


async def check_user(update: Update) -> bool:
    if update.effective_user and update.effective_user.id != ALLOWED_ID:
        await update.message.reply_text('❌ 접근 권한 없음')
        return False
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user(update):
        return
    await update.message.reply_text(
        '📈 <b>MJstock 주식 봇</b>\n\n'
        '• <code>/scan</code> — 전체 스캔\n'
        '• <code>/market</code> — 시장 필터\n'
        '• <code>/stock TICKER</code> — 종목 분석\n'
        '• <code>/watchlist</code> — 관심종목\n'
        '• <code>/mjstock TICKER</code> — 상세 분석',
        parse_mode='HTML'
    )


def _load_stock_handlers():
    from handlers._stock import cmd_stock, cmd_scan, cmd_market, cmd_watchlist, cmd_mjstock
    return cmd_stock, cmd_scan, cmd_market, cmd_watchlist, cmd_mjstock


def main():
    try:
        cmd_stock, cmd_scan, cmd_market, cmd_watchlist, cmd_mjstock = _load_stock_handlers()
    except Exception as e:
        logger.error(f'주식 핸들러 로드 실패: {e}')
        sys.exit(1)

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start',     cmd_start))
    app.add_handler(CommandHandler('stock',     cmd_stock))
    app.add_handler(CommandHandler('scan',      cmd_scan))
    app.add_handler(CommandHandler('market',    cmd_market))
    app.add_handler(CommandHandler('watchlist', cmd_watchlist))
    app.add_handler(CommandHandler('mjstock',   cmd_mjstock))

    logger.info(f'✅ HermesStockBot 시작 (ALLOWED_ID={ALLOWED_ID})')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
