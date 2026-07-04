"""handlers/_stock_mjstock.py — MJstock 주식 검색기 텔레그램 핸들러
(cmd_backtest, cmd_mjscan, cmd_mjstock, cmd_mjbuy, cmd_mjsell, cmd_mjpositions)
"""
import json
import logging
import socket
import subprocess
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
# MJstock 단일 종목 검색식 분석 (/mjstock TICKER)
# ════════════════════════════════════════════════════════════════

MJSTOCK_DIR    = Path("/Users/bluesea/Applications/Mjstock")
SCAN_SINGLE    = MJSTOCK_DIR / "screener" / "scan_single.py"
SCAN_ALL       = MJSTOCK_DIR / "screener" / "scan_all_ticker.py"
VENV_PYTHON    = MJSTOCK_DIR / ".venv" / "bin" / "python"
DASHBOARD_PORT = 8765

US_SCREENERS = [
    ("우량주농사",    "uryangju"),
    ("세력주농사",    "selyeok"),
    ("세력포착",      "pochak"),
    ("주도주단기",    "judoju"),
    ("단타의신",      "danta"),
    ("슈팅",         "shooting"),
    ("단타추돌이",    "chuddoli"),
    ("초우량주",      "chowuryang"),
]
KR_SCREENERS = [
    ("우량주(KR)",   "uryangju_kr"),
    ("세력주(KR)",   "selyeok_kr"),
    ("세력포착(KR)", "pochak_kr"),
    ("주도주(KR)",   "judoju_kr"),
    ("단타(KR)",     "danta_kr"),
    ("초우량(KR)",   "chowuryang_kr"),
]


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


async def cmd_mjstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mjstock TICKER — 한국/미국 선택 후 모든 검색기 전체 분석"""
    args = context.args or []
    if not args:
        await safe_reply(update.message,
            "📈 사용법:\n"
            "  <code>/mjstock NVDA</code>   — 미국 주식\n"
            "  <code>/mjstock 005930</code> — 한국 주식\n\n"
            "모든 검색기로 분석 후 점수 순으로 결과를 보여줍니다.",
            parse_mode="HTML")
        return

    ticker = args[0].upper()
    is_kr = ticker.isdigit() and len(ticker) == 6

    if is_kr:
        keyboard = [[
            InlineKeyboardButton("🇰🇷 한국 전체 검색", callback_data=f"mjstock_all:{ticker}:kr")
        ]]
        msg = f"📊 <b>{ticker}</b> — 한국 모든 검색기로 분석합니다"
    else:
        keyboard = [[
            InlineKeyboardButton("🇺🇸 미국 전체 검색", callback_data=f"mjstock_all:{ticker}:us"),
            InlineKeyboardButton("🇰🇷 한국으로도 검색", callback_data=f"mjstock_all:{ticker}:kr"),
        ]]
        msg = f"📊 <b>{ticker}</b> — 어느 시장으로 검색할까요?"

    await safe_reply(update.message, msg, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))


async def _handle_mjstock_all(query, ticker: str, market: str):
    """모든 검색기 전체 분석 → 점수 순 결과 + 검색기별 버튼"""
    flag = "🇺🇸" if market == "us" else "🇰🇷"
    await query.edit_message_text(f"⏳ {flag} <b>{ticker}</b> 모든 검색기 분석 중...\n약 30~60초 소요됩니다.", parse_mode="HTML")

    py = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"

    try:
        proc = subprocess.run(
            [py, str(SCAN_ALL), "--ticker", ticker, "--market", market],
            capture_output=True, text=True,
            cwd=str(MJSTOCK_DIR / "screener"),
            timeout=120,
        )
        json_line = next((l for l in proc.stdout.splitlines() if l.strip().startswith('{')), '')
        res = json.loads(json_line)
    except subprocess.TimeoutExpired:
        await query.edit_message_text("⏱ 타임아웃 (120초 초과)")
        return
    except Exception as e:
        await query.edit_message_text(f"❌ 오류: {e}\n{proc.stderr[:200] if 'proc' in dir() else ''}")
        return

    if not res.get("ok"):
        await query.edit_message_text(f"❌ {res.get('error', '분석 실패')}")
        return

    results = res["results"]
    if not results:
        await query.edit_message_text(f"❌ {ticker} — 결과 없음")
        return

    lines = [f"{flag} <b>{ticker}</b> — 검색기 전체 분석 결과\n"]
    for i, r in enumerate(results[:8], 1):
        icon_pass = "✅" if r["pass"] else "❌"
        filled = round(r["score"] / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"{i}. {r['icon']} <b>{r['name']}</b>  {r['score']:.0f}점 {icon_pass}")
        lines.append(f"   [{bar}]")

    lines.append("\n▼ 검색기를 눌러 차트 확인")

    keyboard = []
    row = []
    for r in results[:8]:
        icon_pass = "✅" if r["pass"] else "❌"
        label = f"{r['icon']} {r['name']} {r['score']:.0f}점 {icon_pass}"
        btn = InlineKeyboardButton(label, callback_data=f"mjstock_chart:{ticker}:{r['key']}")
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    ip = _get_local_ip()
    keyboard.append([InlineKeyboardButton(
        "📋 대시보드 목록", url=f"http://{ip}:{DASHBOARD_PORT}/"
    )])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ════════════════════════════════════════════════════════════════
# 스캔 결과 종목 목록 (mjstock_scan_list 콜백)
# ════════════════════════════════════════════════════════════════

_MJSTOCK_SCREENER_NAMES = {
    "uryangju": "🌾 우량주농사", "selyeok": "🔮 세력주농사",
    "pochak": "🎯 세력포착", "judoju": "⚡ 주도주단기",
    "danta": "🔄 단타의신", "shooting": "🚀 슈팅",
    "chuddoli": "💥 단타추돌이", "chowuryang": "👑 초우량주",
    "samdoli": "🔺 삼돌이(US)", "nongsa_danta": "🌾🔄 농사단타",
    "uryangju_kr": "🌾 우량주(KR)", "selyeok_kr": "🔮 세력주(KR)",
    "pochak_kr": "🎯 세력포착(KR)", "judoju_kr": "⚡ 주도주(KR)",
    "danta_kr": "🔄 단타(KR)", "chowuryang_kr": "👑 초우량(KR)",
    "samdoli_kr": "🔺 삼돌이(KR)", "nongsa_danta_kr": "🌾🔄 농사단타(KR)",
}


_SCAN_BATCH_WINDOW_SEC = 30 * 60  # 배치 매니페스트가 없는 구버전 메시지용 근사 폭(±30분)


def _count_valid_tickers(csv_path, screener_key: str) -> int:
    """CSV의 실제 code/ticker 컬럼 기준 통과 종목 수.
    column 0(scan_datetime)은 항상 채워져 있어 세는 기준으로 쓰면 안 됨 —
    버튼에 찍힌 개수와 클릭했을 때 나오는 목록이 어긋나 "통과 종목 없음" 오표시가 남."""
    try:
        import pandas as _pd
        df = _pd.read_csv(csv_path, dtype={"code": str, "ticker": str})
        id_col = "code" if screener_key.endswith("_kr") else "ticker"
        if id_col not in df.columns:
            id_col = next((c for c in ("ticker", "code") if c in df.columns), None)
        if id_col is None:
            return 0
        return len(df[df[id_col].notna() & (df[id_col].astype(str).str.strip() != "nan")])
    except Exception:
        return 0


async def _handle_mjstock_scan_summary(query, id_str: str):
    """mjstock_scan_summary 콜백 — 검색기 버튼 목록 재표시

    results/_batch/{id_str}.json에 "이 메시지가 원래 보냈던 정확한 검색기 목록"이
    저장되어 있으면(send_scan_result.py / auto_scan_morning.py가 발송 시 기록) 그것만
    그대로 복원한다. 매니페스트가 없는 구버전 콜백(과거 메시지)은 date_str 기반
    ±30분 창 근사로 폴백 — 단, 이 경우 무관한 배치가 섞여 보일 수 있음(알려진 한계).
    """
    import glob as _glob
    import json as _json
    import datetime as _dt

    results_dir = MJSTOCK_DIR / "results"
    batch_path  = results_dir / "_batch" / f"{id_str}.json"
    buttons = []
    found_any = False

    if batch_path.exists():
        try:
            manifest = _json.loads(batch_path.read_text())
        except Exception:
            manifest = {}
        for key, file_date_str in manifest.get("screeners", {}).items():
            name = _MJSTOCK_SCREENER_NAMES.get(key, key)
            csv_path = results_dir / key / f"scan_{file_date_str}.csv"
            if not csv_path.exists():
                continue
            cnt = _count_valid_tickers(csv_path, key)
            if cnt > 0:
                buttons.append(InlineKeyboardButton(
                    f"{name}({cnt}종)",
                    callback_data=f"mjstock_scan_list:{key}:{file_date_str}:{id_str}"
                ))
                found_any = True
        date_prefix = id_str[:8]
    else:
        date_prefix = id_str[:8]
        ref_dt = None
        try:
            ref_dt = _dt.datetime.strptime(id_str, "%Y%m%d_%H%M%S")
        except ValueError:
            pass

        for key, name in _MJSTOCK_SCREENER_NAMES.items():
            pattern = str(results_dir / key / f"scan_{date_prefix}_*.csv")
            files = sorted(_glob.glob(pattern))
            if ref_dt is not None:
                def _in_batch_window(fp):
                    try:
                        fdt = _dt.datetime.strptime(Path(fp).stem.replace("scan_", ""), "%Y%m%d_%H%M%S")
                    except ValueError:
                        return False
                    return abs((fdt - ref_dt).total_seconds()) <= _SCAN_BATCH_WINDOW_SEC
                files = [f for f in files if _in_batch_window(f)]
            if not files:
                continue
            csv_path = files[-1]
            file_date_str = Path(csv_path).stem.replace("scan_", "")
            cnt = _count_valid_tickers(csv_path, key)
            if cnt > 0:
                buttons.append(InlineKeyboardButton(
                    f"{name}({cnt}종)",
                    callback_data=f"mjstock_scan_list:{key}:{file_date_str}"
                ))
                found_any = True

    if not found_any:
        await query.edit_message_text(f"⚠️ {date_prefix} 스캔 결과 없음")
        return

    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    label = f"{date_prefix[:4]}-{date_prefix[4:6]}-{date_prefix[6:]}"
    await query.edit_message_text(
        f"📊 <b>{label} 스캔 결과</b>\n검색기를 선택하세요:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_mjstock_scan_list(query, screener_key: str, date_str: str, batch_id: str = None):
    """mjstock_scan_list 콜백 — 스캔 결과 CSV에서 종목 목록 표시"""
    import pandas as _pd

    _summary_id = batch_id or date_str

    # 아래 조기 종료 분기들은 전부 이 뒤로가기 버튼을 달아야 함 —
    # reply_markup 없이 edit_message_text만 하면 키보드가 통째로 사라져
    # 사용자가 더 이상 아무 버튼도 못 누르는 막다른 화면이 됨
    _back_kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "← 검색기 목록으로", callback_data=f"mjstock_scan_summary:{_summary_id}")]])

    csv_path = MJSTOCK_DIR / "results" / screener_key / f"scan_{date_str}.csv"
    if not csv_path.exists():
        await query.edit_message_text("⚠️ 결과 없음 — 스캔 결과가 만료되었거나 없습니다.", reply_markup=_back_kb)
        return

    try:
        # code/ticker를 dtype 지정 없이 읽으면 "036930" 같은 KR 종목코드가
        # 정수로 자동 변환되어 앞자리 0이 사라짐(036930→36930) → 이후 차트/점수 조회 전부 실패
        df = _pd.read_csv(csv_path, dtype={"code": str, "ticker": str})
    except Exception as e:
        await query.edit_message_text(f"❌ CSV 읽기 오류: {e}", reply_markup=_back_kb)
        return

    id_col = "code" if screener_key.endswith("_kr") else "ticker"
    if id_col not in df.columns:
        id_col = next((c for c in ("ticker", "code") if c in df.columns), df.columns[0])

    df = df[df[id_col].notna() & (df[id_col].astype(str).str.strip() != "nan")]
    if df.empty:
        await query.edit_message_text("⚠️ 통과 종목 없음", reply_markup=_back_kb)
        return

    # 신호 먼저, 점수 높은 순
    df = df.copy()
    if "has_buy_signal" in df.columns:
        df["_sig"] = df["has_buy_signal"].map(lambda x: 1 if str(x).lower() == "true" else 0)
    else:
        df["_sig"] = 0
    if "score" not in df.columns:
        df["score"] = 0
    df = df.sort_values(["_sig", "score"], ascending=[False, False]).reset_index(drop=True)

    screener_display = _MJSTOCK_SCREENER_NAMES.get(screener_key, screener_key)
    total = len(df)

    lines = [f"📊 <b>{screener_display}</b> — {total}종 (신호 먼저, 점수순)\n"]
    for i, row in df.iterrows():
        rank = i + 1
        tkr  = str(row[id_col])
        score = int(row.get("score", 0))
        has_sig = row.get("_sig", 0) == 1
        days_ago = row.get("buy_signal_days_ago")

        if has_sig:
            if days_ago is not None and str(days_ago) not in ("nan", ""):
                d = int(float(days_ago))
                sig_lbl = "오늘" if d == 0 else f"{d}일전"
            else:
                sig_lbl = "신호"
            lines.append(f"🟢 #{rank} <b>{tkr}</b> {score}점 🟢{sig_lbl}")
        else:
            lines.append(f"📈 #{rank} <b>{tkr}</b> {score}점")

    lines.append("\n▼ 종목 클릭 → 차트 + 분석")

    keyboard, row_btns = [], []
    for i, row in df.iterrows():
        rank = i + 1
        tkr  = str(row[id_col])
        score = int(row.get("score", 0))
        has_sig = row.get("_sig", 0) == 1
        days_ago = row.get("buy_signal_days_ago")

        if has_sig:
            d = int(float(days_ago)) if days_ago is not None and str(days_ago) not in ("nan", "") else 0
            sig_lbl = "오늘" if d == 0 else f"{d}일전"
            label = f"🟢 #{rank} {tkr} {score}점 🟢{sig_lbl}"
        else:
            label = f"📈 #{rank} {tkr} {score}점"

        row_btns.append(InlineKeyboardButton(
            label, callback_data=f"mjstock_chart:{tkr}:{screener_key}:{date_str}:{_summary_id}"))
        if len(row_btns) == 2:
            keyboard.append(row_btns)
            row_btns = []
    if row_btns:
        keyboard.append(row_btns)

    # 다른 검색기 결과로 돌아갈 수 있는 back 버튼
    keyboard.append([InlineKeyboardButton("← 다른 검색기 보기", callback_data=f"mjstock_scan_summary:{_summary_id}")])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_mjstock_chart(query, ticker: str, screener_key: str, date_str: str = None, batch_id: str = None):
    """검색기 버튼 클릭 → 상세 결과 + 차트 파일 첨부"""
    import glob as _glob

    py = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"

    await query.edit_message_text(f"⏳ {ticker} × {screener_key} 분석 중...")

    # 에러/타임아웃 분기도 뒤로가기 버튼 없이 edit하면 막다른 화면이 됨
    _list_back_cb = f"mjstock_scan_list:{screener_key}:{date_str}" + (f":{batch_id}" if batch_id else "")
    _err_back_kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "← 검색기 목록으로",
        callback_data=_list_back_cb if date_str else f"mjstock_scan_summary:{screener_key}"
    )]])

    # subprocess.run()을 그대로 쓰면 이벤트 루프가 블로킹되어 그동안 다른 버튼 클릭이
    # 전부 밀리고(Query too old 원인) 응답도 느려짐 — _stock_coin.py와 동일하게 스레드로 분리
    import concurrent.futures, asyncio as _aio
    loop = _aio.get_event_loop()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            proc = await _aio.wait_for(
                loop.run_in_executor(pool, lambda: subprocess.run(
                    [py, str(SCAN_SINGLE), "--ticker", ticker, "--screener", screener_key],
                    capture_output=True, text=True,
                    cwd=str(MJSTOCK_DIR / "screener"),
                    timeout=60,
                )),
                timeout=65,
            )
        json_line = next((l for l in proc.stdout.splitlines() if l.strip().startswith('{')), '')
        res = json.loads(json_line)
    except (subprocess.TimeoutExpired, _aio.TimeoutError):
        await query.edit_message_text("⏱ 타임아웃 (60초 초과)", reply_markup=_err_back_kb)
        return
    except Exception as e:
        await query.edit_message_text(f"❌ 오류: {e}", reply_markup=_err_back_kb)
        return

    if not res.get("ok"):
        await query.edit_message_text(f"❌ {res.get('error', '분석 실패')}", reply_markup=_err_back_kb)
        return

    score  = res.get("score", 0)
    filled = round(score / 100 * 10)
    bar    = "█" * filled + "░" * (10 - filled)

    bool_items = [(k, v) for k, v in res.items()
                  if isinstance(v, bool)
                  and not k.startswith("exp_")
                  and not k.startswith("entry_")
                  and k not in ("ok", "pass")]
    pass_cnt  = sum(1 for _, v in bool_items if v)
    total_cnt = len(bool_items)
    cond_lines = [f"{'✅' if v else '❌'} {k}" for k, v in bool_items[:12]]

    lines = [
        f"📊 <b>{ticker}</b>  [{screener_key}]",
        f"",
        f"점수: <b>{score:.0f}점</b>  [{bar}]",
        f"조건: {pass_cnt}/{total_cnt} 통과",
        f"",
    ] + cond_lines

    market = "kr" if screener_key.endswith("_kr") else "us"
    if date_str:
        screener_display = _MJSTOCK_SCREENER_NAMES.get(screener_key, screener_key)
        back_label = f"← {screener_display} 목록으로"
        back_cb    = _list_back_cb
    else:
        back_label = "← 전체 결과 목록으로"
        back_cb    = f"mjstock_all:{ticker}:{market}"
    keyboard = [[InlineKeyboardButton(back_label, callback_data=back_cb)]]

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # ── 퀀트 DB 기록 (수동 조회도 추적 대상) ─────────────────────
    try:
        import sys as _sys
        _tracker_path = str(MJSTOCK_DIR / "screener")
        if _tracker_path not in _sys.path:
            _sys.path.insert(0, _tracker_path)
        from signal_tracker import record_manual_lookup
        # 통과 조건 키를 시그널 목록으로 사용 (점수 계산은 score 직접 전달)
        _passed_signals = [k for k, v in bool_items if v]
        record_manual_lookup(
            ticker=ticker,
            market=market,
            screener=screener_key,
            close_price=res.get("close_price"),
            atr14=None,
            signals=_passed_signals,
            score=int(score),
            note="mjstock_manual",
        )
    except Exception as _te:
        logger.debug(f"[Tracker] mjstock_chart 기록 실패: {_te}")

    charts_dir = MJSTOCK_DIR / "charts"
    chart_files = sorted(_glob.glob(str(charts_dir / screener_key / f"{ticker}_farming_*.html")))
    chart_file  = chart_files[-1] if chart_files else None

    if chart_file:
        try:
            with open(chart_file, "rb") as f:
                await query.message.reply_document(
                    document=f,
                    filename=f"{ticker}_{screener_key}.html",
                    caption=f"📊 {ticker} [{screener_key}] 차트\n다운로드 후 브라우저로 열기",
                )
        except Exception:
            pass


async def callback_mjstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """mjstock 계열 인라인 버튼 통합 핸들러"""
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data.startswith("mjstock_scan_summary:"):
        # mjstock_scan_summary:{date_str}
        date_str = data.split(":", 1)[1]
        await _handle_mjstock_scan_summary(query, date_str)
        return

    if data.startswith("mjstock_scan_list:"):
        # mjstock_scan_list:{screener_key}:{date_str}[:{batch_id}]
        parts = data.split(":", 3)
        if len(parts) >= 3:
            screener_key = parts[1]
            date_str     = parts[2]
            batch_id     = parts[3] if len(parts) == 4 else None
            await _handle_mjstock_scan_list(query, screener_key, date_str, batch_id=batch_id)
        return

    if data.startswith("mjstock_all:"):
        parts = data.split(":")
        if len(parts) == 3:
            _, ticker, market = parts
            await _handle_mjstock_all(query, ticker, market)
        return

    if data.startswith("mjstock_chart:"):
        # mjstock_chart:{ticker}:{screener_key}[:{date_str}[:{batch_id}]]
        parts = data.split(":", 4)
        if len(parts) >= 3:
            ticker       = parts[1]
            screener_key = parts[2]
            date_str     = parts[3] if len(parts) >= 4 and parts[3] else None
            batch_id     = parts[4] if len(parts) == 5 and parts[4] else None
            await _handle_mjstock_chart(query, ticker, screener_key, date_str=date_str, batch_id=batch_id)
        return

    # 기존 호환: mjstock:TICKER:SCREENER
    parts = data.split(":")
    if len(parts) == 3 and parts[0] == "mjstock":
        _, ticker, screener_key = parts
        if screener_key == "noop":
            return
        await _handle_mjstock_chart(query, ticker, screener_key)
        return


# ════════════════════════════════════════════════════════════════
# MJstock 스캔 결과 보기 콜백 (아침/장중 스캔 [결과 보기] 버튼)
# callback_data: mjstock_results__검색기키__YYYYMMDD
# ════════════════════════════════════════════════════════════════
async def callback_mjstock_results(update, context) -> None:
    query = update.callback_query
    await query.answer("결과 파일 준비 중...")

    parts = (query.data or "").split("__")
    if len(parts) < 3:
        await query.answer("잘못된 요청")
        return

    screener_key = parts[1]
    date_str     = parts[2]

    mjstock_dir = Path("/Users/bluesea/Applications/Mjstock")
    html_path   = mjstock_dir / "results" / "_html" / f"results_{screener_key}_{date_str}.html"

    if not html_path.exists():
        try:
            import sys
            sys.path.insert(0, str(mjstock_dir))
            from generate_results_html import generate_results_html, SCREENER_NAMES
            gen = generate_results_html(screener_key, date_str)
            if gen and gen.exists():
                html_path = gen
            else:
                await query.answer("❌ 결과 없음")
                return
        except Exception as e:
            logger.error(f"results HTML 생성 실패: {e}")
            await query.answer("❌ 파일 생성 실패")
            return

    try:
        import sys
        sys.path.insert(0, str(mjstock_dir))
        from generate_results_html import SCREENER_NAMES
    except Exception:
        SCREENER_NAMES = {}

    name = SCREENER_NAMES.get(screener_key, screener_key)
    caption = f"📊 <b>{name}</b> 결과\n{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    with open(html_path, "rb") as f:
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=f,
            filename=html_path.name,
            caption=caption,
            parse_mode="HTML",
        )


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
