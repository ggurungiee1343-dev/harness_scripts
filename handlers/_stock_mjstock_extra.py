"""handlers/_stock_mjstock_extra.py — MJstock 부가 명령어
(cmd_backtest, cmd_mjscan, cmd_mjbuy, cmd_mjsell, cmd_mjpositions)

2026-07-04: _stock_mjstock.py(808줄)에서 분리. 네비게이션 핵심부
(cmd_mjstock/callback_mjstock/callback_mjstock_results — 오늘 BUG-MJS-015~021
수정한 부분)는 그대로 두고, 서로 독립적인 부가 명령어만 이쪽으로 이동.
"""
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


async def safe_reply(message, text: str, **kwargs):
    """Markdown 파싱 실패 시 plain text 자동 폴백 (패키지 독립성 유지 위해 로컬 정의)"""
    try:
        return await message.reply_text(text, **kwargs)
    except Exception as e:
        if 'parse' in str(e).lower() or 'entit' in str(e).lower():
            kwargs.pop('parse_mode', None)
            return await message.reply_text(text, **kwargs)
        raise


async def safe_edit(message, text: str, **kwargs):
    """edit_text용 Markdown 폴백 — safe_reply와 동일 패턴"""
    try:
        return await message.edit_text(text, **kwargs)
    except Exception as e:
        if 'parse' in str(e).lower() or 'entit' in str(e).lower():
            kwargs.pop('parse_mode', None)
            return await message.edit_text(text, **kwargs)
        raise


# ════════════════════════════════════════════════════════════════
# /backtest — 매매 결과 통계
# ════════════════════════════════════════════════════════════════

async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/backtest — 매매 결과 통계"""
    import sqlite3
    from modules.stock_fetcher import CACHE_DB

    try:
        with sqlite3.connect(CACHE_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT, symbol TEXT,
                    entry_price REAL, exit_price REAL, shares INTEGER,
                    pnl_pct REAL, pnl_dollar REAL, notes TEXT
                )
            """)
            rows = conn.execute(
                "SELECT symbol, pnl_pct, pnl_dollar, notes, recorded_at "
                "FROM trade_results ORDER BY recorded_at DESC"
            ).fetchall()
    except Exception as e:
        await safe_reply(update.message, f"❌ 데이터 오류: {e}")
        return

    if not rows:
        await safe_reply(update.message,
            "📊 기록된 매매 없음\n`/result TICKER 매수가 매도가`로 기록하세요."
        )
        return

    wins   = [r for r in rows if r[1] > 0]
    losses = [r for r in rows if r[1] <= 0]
    total  = len(rows)
    win_r  = len(wins) / total * 100
    avg    = sum(r[1] for r in rows) / total
    avg_w  = sum(r[1] for r in wins)  / len(wins)  if wins   else 0
    avg_l  = sum(r[1] for r in losses)/ len(losses) if losses else 0
    rr     = abs(avg_w / avg_l) if avg_l != 0 else 0

    best  = max(rows, key=lambda r: r[1])
    worst = min(rows, key=lambda r: r[1])

    lines = [
        f"📊 *V\\_FINAL 매매 성과* ({total}건)",
        f"",
        f"승률: *{win_r:.1f}%* ({len(wins)}승 {len(losses)}패)",
        f"평균 수익률: *{avg:+.2f}%*",
        f"평균 이익: {avg_w:+.2f}%  |  평균 손실: {avg_l:+.2f}%",
        f"리스크:리워드 = 1 : {rr:.2f}",
        f"",
        f"🏆 최고: {best[0]} {best[1]:+.2f}%",
        f"💀 최악: {worst[0]} {worst[1]:+.2f}%",
        f"",
        f"*최근 5건*",
    ]
    for sym, pnl, dollar, note, ts in rows[:5]:
        icon = "🟢" if pnl > 0 else "🔴"
        d_str = f" (${dollar:+.0f})" if dollar else ""
        lines.append(f"{icon} {sym} {pnl:+.2f}%{d_str}  {ts[:10]}")

    await safe_reply(update.message, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ════════════════════════════════════════════════════════════════
# MJstock 나스닥 500 자동 스캔
# ════════════════════════════════════════════════════════════════

async def cmd_mjscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mjscan — MJstock 나스닥 500 자동 스캔 + 텔레그램 발송

    사용:
      /mjscan         — 8개 검색식 모두 실행
      /mjscan selyeok — 특정 검색식만 실행
    """
    import subprocess
    import sys
    from pathlib import Path

    mjstock_dir = Path("/Users/bluesea/Applications/Mjstock")
    auto_scan_script = mjstock_dir / "auto_scan_nasdaq500.py"

    if not auto_scan_script.exists():
        await safe_reply(update.message, "❌ auto_scan_nasdaq500.py 파일 없음")
        return

    msg = await safe_reply(update.message, "⏳ MJstock 나스닥 500 스캔 시작...")

    try:
        result = subprocess.run(
            [sys.executable, str(auto_scan_script)],
            capture_output=True,
            text=True,
            cwd=str(mjstock_dir),
            timeout=600,
        )

        if result.returncode == 0:
            lines = ["✅ MJstock 나스닥 500 스캔 완료!"]
            if "✅" in result.stdout:
                success_lines = [l for l in result.stdout.split('\n') if '✅' in l]
                lines.extend(success_lines[:10])

            await safe_edit(msg, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        else:
            await safe_edit(msg, f"❌ 스캔 실패\n```\n{result.stderr[:500]}\n```", parse_mode=ParseMode.MARKDOWN)

    except subprocess.TimeoutExpired:
        await safe_edit(msg, "⏱️ 스캔 타임아웃 (10분 초과)", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await safe_edit(msg, f"❌ 오류: {str(e)[:200]}", parse_mode=ParseMode.MARKDOWN)


# ════════════════════════════════════════════════════════════════
# MJstock 포지션 모니터 명령어 (POSITION_MONITOR_ENABLED=True 시 활성)
# ════════════════════════════════════════════════════════════════
POSITION_MONITOR_ENABLED = False  # True로 변경 시 /mjbuy /mjsell /mjpositions 활성화

_MJSTOCK_DIR = Path("/Users/bluesea/Applications/Mjstock")


async def cmd_mjbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mjbuy TICKER 진입가 수량 검색기키 — 포지션 매수 기록
    예: /mjbuy AAPL 180 100 uryangju
    """
    if not POSITION_MONITOR_ENABLED:
        await safe_reply(update.message, "⚠️ 포지션 모니터 비활성 상태입니다.")
        return
    import sys as _sys
    _sys.path.insert(0, str(_MJSTOCK_DIR / "sellstock"))
    from position_store import add_position
    args = context.args or []
    if len(args) < 4:
        await safe_reply(update.message,
            "사용법: /mjbuy TICKER 진입가 수량 검색기키\n예: /mjbuy AAPL 180 100 uryangju")
        return
    ticker, price, shares, screener = args[0], float(args[1]), int(args[2]), args[3]
    samdoli_type = args[4] if len(args) > 4 else None
    pos = add_position(ticker, price, shares, screener, samdoli_type=samdoli_type)
    await safe_reply(update.message,
        f"✅ 포지션 등록\n"
        f"[{ticker}] {price:,.2f} × {shares}주\n"
        f"검색기: {screener} | ID: {pos['id']}\n"
        f"등록일: {pos['entry_date']}")


async def cmd_mjsell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mjsell ID 청산가 — 포지션 청산 기록
    예: /mjsell 1 195.5
    """
    if not POSITION_MONITOR_ENABLED:
        await safe_reply(update.message, "⚠️ 포지션 모니터 비활성 상태입니다.")
        return
    import sys as _sys
    _sys.path.insert(0, str(_MJSTOCK_DIR / "sellstock"))
    from position_store import close_position, load_positions
    args = context.args or []
    if not args:
        await safe_reply(update.message, "사용법: /mjsell ID 청산가\n예: /mjsell 1 195.5")
        return
    pos_id     = int(args[0])
    exit_price = float(args[1]) if len(args) > 1 else None
    ok = close_position(pos_id, exit_price)
    if ok:
        msg = f"✅ 포지션 #{pos_id} 청산 완료"
        if exit_price:
            msg += f"\n청산가: {exit_price:,.2f}"
        await safe_reply(update.message, msg)
    else:
        await safe_reply(update.message, f"❌ 포지션 #{pos_id} 없음")


async def cmd_mjpositions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mjpositions — 보유 포지션 목록"""
    if not POSITION_MONITOR_ENABLED:
        await safe_reply(update.message, "⚠️ 포지션 모니터 비활성 상태입니다.")
        return
    import sys as _sys
    _sys.path.insert(0, str(_MJSTOCK_DIR / "sellstock"))
    from position_store import get_active_positions
    positions = get_active_positions()
    if not positions:
        await safe_reply(update.message, "보유 포지션 없음")
        return
    lines = ["📂 <b>보유 포지션</b>\n"]
    for p in positions:
        lines.append(
            f"<b>[{p['ticker']}]</b> #{p['id']} — {p['screener_key']}\n"
            f"  진입: {p['entry_price']:,.2f} × {p['shares']}주 ({p['entry_date']})"
        )
    await safe_reply(update.message, "\n".join(lines), parse_mode="HTML")
