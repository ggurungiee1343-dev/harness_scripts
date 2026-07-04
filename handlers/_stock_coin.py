"""handlers/_stock_coin.py — 코인 분석 텔레그램 핸들러 (/coin 명령어)"""
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

COIN_DIR = Path("/Users/bluesea/Applications/Mjstock/Coin")

# /coin all 스캔 결과 임시 캐시 (ts_key -> scan_out, 최대 3개)
_coin_scan_cache: dict[str, dict] = {}


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


def _fmt_price(v) -> str:
    """코인 가격을 한국식 단위로 포맷 (억/만/원)"""
    if v is None:
        return "—"
    if v >= 100_000_000:
        return f"{v / 100_000_000:.2f}억"
    if v >= 10_000:
        return f"{v / 10_000:.1f}만"
    if v >= 1:
        return f"{v:,.0f}"
    return f"{v:.6f}"


def _coin_sys_path():
    import sys as _sys
    if str(COIN_DIR) not in _sys.path:
        _sys.path.insert(0, str(COIN_DIR))
        _sys.path.insert(1, str(COIN_DIR / ".." / "screener"))


async def cmd_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/coin all   — 빗썸 전체 코인 6개 검색기 스캔
    /coin ETH   — ETH 6개 검색기 전체 분석
    """
    args = context.args or []
    if not args:
        await safe_reply(update.message,
            "🪙 <b>코인 분석 명령어</b>\n\n"
            "<code>/coin all</code>   — 빗썸 전체 코인 6개 검색기 스캔\n"
            "<code>/coin ETH</code>   — ETH 6개 검색기 전체 분석\n\n"
            "각 검색기별 점수·시그널 + 차트 버튼 제공\n"
            "데이터: 빗썸 Public API (인증 불필요)",
            parse_mode="HTML")
        return

    sub = args[0].lower()

    # ── /coin all / /coin scan ──────────────────────────────────
    if sub in ("all", "scan"):
        msg = await safe_reply(update.message, "⏳ 코인 전체 스캔 중... (~90초)")
        try:
            _coin_sys_path()
            from run_scan_coin import run_scan, SCREENERS as _SC
            import concurrent.futures, asyncio
            from datetime import datetime
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                scan_out = await asyncio.wait_for(
                    loop.run_in_executor(pool, lambda: run_scan(send_telegram=False)),
                    timeout=240,
                )
            # 캐시 저장 (최대 3개 유지)
            ts_key = datetime.now().strftime("%Y%m%d_%H%M%S")
            _coin_scan_cache[ts_key] = scan_out
            if len(_coin_scan_cache) > 3:
                oldest = sorted(_coin_scan_cache.keys())[0]
                del _coin_scan_cache[oldest]

            total = sum(len(v["results"]) for v in scan_out.values())
            lines = [f"🪙 <b>MJcoin 스캔 완료</b> — {total}건 통과\n"]
            for key, data in scan_out.items():
                results = data.get("results", [])
                s = _SC[key]
                if results:
                    sig_cnt   = sum(1 for r in results if r.get("grade") in ("A", "B"))
                    score_cnt = sum(1 for r in results if r.get("grade") == "A")
                    part = f"신호({sig_cnt}개), 점수({score_cnt}개)"
                else:
                    part = "(없음)"
                lines.append(f"{s['icon']} {s['name']}: {part}")
            lines.append("\n▼ 검색기를 눌러 종목 목록 확인")

            keyboard, row_btns = [], []
            for key, data in scan_out.items():
                s = _SC[key]
                cnt = len(data.get("results", []))
                label = f"{s['icon']} {s['name']} 검색({cnt}종)"
                row_btns.append(InlineKeyboardButton(
                    label, callback_data=f"coin_scan_list:{ts_key}:{key}"))
                if len(row_btns) == 2:
                    keyboard.append(row_btns); row_btns = []
            if row_btns:
                keyboard.append(row_btns)

            await safe_edit(msg, "\n".join(lines),
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(keyboard))
        except asyncio.TimeoutError:
            await safe_edit(msg, "⏱ 스캔 타임아웃 (240초 초과)")
        except Exception as e:
            logger.error(f"/coin all 오류: {e}", exc_info=True)
            await safe_edit(msg, f"❌ 오류: {e}")
        return

    # ── /coin SYMBOL ────────────────────────────────────────────
    symbol = sub.upper()
    keyboard = [[InlineKeyboardButton(
        f"🔍 {symbol} — 6개 검색기 전체 분석",
        callback_data=f"coin_all:{symbol}"
    )]]
    await safe_reply(update.message,
        f"🪙 <b>{symbol}</b> — 어떤 분석을 할까요?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))


async def _handle_coin_scan_list(query, ts_key: str, screener_key: str):
    """coin_scan_list 콜백 — 전체 스캔 결과에서 특정 검색기 종목 목록 표시"""
    _coin_sys_path()
    from run_scan_coin import SCREENERS, _signal_label

    scan_out = _coin_scan_cache.get(ts_key)
    if not scan_out:
        await query.edit_message_text("⚠️ 스캔 결과가 만료됐습니다. /coin all 로 다시 스캔해주세요.")
        return

    s = SCREENERS.get(screener_key, {"name": screener_key, "icon": "📊"})
    results = scan_out.get(screener_key, {}).get("results", [])

    if not results:
        await query.edit_message_text(
            f"{s['icon']} <b>{s['name']}</b> — 통과 종목 없음",
            parse_mode="HTML")
        return

    sorted_results = sorted(results, key=lambda x: -x.get("score", 0))

    lines = [f"{s['icon']} <b>{s['name']}</b> — {len(results)}종 통과 (점수순)\n"]
    for r in sorted_results[:15]:
        grade = r.get("grade", "?")
        score = r.get("score", 0)
        chg   = r.get("change_pct", 0) or 0
        close = r.get("close")
        atr14 = r.get("atr14")
        btcw  = " ⚠️BTC" if r.get("btc_warning") else ""
        sig   = _signal_label(screener_key, r)

        grade_icon = "🔥" if grade == "A" else "✅" if grade == "B" else "·"
        chg_str    = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        sig_str    = f"  <i>{sig}</i>" if sig else ""
        lines.append(f"{grade_icon} <b>{r['symbol']}</b> {score}점 ({chg_str}){btcw}{sig_str}")
        if close:
            price_line = f"   현재: {_fmt_price(close)}"
            if atr14 and atr14 > 0:
                stop = close - atr14
                tgt  = close + atr14 * 2
                price_line += f" | 손절: {_fmt_price(stop)} | 목표: {_fmt_price(tgt)}"
            lines.append(price_line)

    if len(results) > 15:
        lines.append(f"\n+{len(results)-15}종 더")
    lines.append("\n▼ 종목 클릭 → 6검색기 전체 분석")

    keyboard, row_btns = [], []
    for r in sorted_results[:18]:
        score = r.get("score", 0)
        grade = r.get("grade", "?")
        g_icon = "🔥" if grade == "A" else "✅" if grade == "B" else "·"
        label = f"{g_icon} {r['symbol']} {score}점"
        row_btns.append(InlineKeyboardButton(
            label, callback_data=f"coin_all:{r['symbol']}:{ts_key}:{screener_key}"))
        if len(row_btns) == 2:
            keyboard.append(row_btns); row_btns = []
    if row_btns:
        keyboard.append(row_btns)
    keyboard.append([InlineKeyboardButton(
        "← 검색기 목록으로", callback_data=f"coin_scan_back:{ts_key}")])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))


async def _handle_coin_scan_back(query, ts_key: str):
    """← 검색기 목록으로 돌아가기"""
    _coin_sys_path()
    from run_scan_coin import SCREENERS as _SC

    scan_out = _coin_scan_cache.get(ts_key)
    if not scan_out:
        await query.edit_message_text("⚠️ 스캔 결과가 만료됐습니다. /coin all 로 다시 스캔해주세요.")
        return

    total = sum(len(v.get("results", [])) for v in scan_out.values())
    lines = [f"🪙 <b>MJcoin 스캔 결과</b> — {total}건 통과\n"]
    for key, data in scan_out.items():
        s = _SC[key]
        results = data.get("results", [])
        if results:
            sig_cnt   = sum(1 for r in results if r.get("grade") in ("A", "B"))
            score_cnt = sum(1 for r in results if r.get("grade") == "A")
            part = f"신호({sig_cnt}개), 점수({score_cnt}개)"
        else:
            part = "(없음)"
        lines.append(f"{s['icon']} {s['name']}: {part}")
    lines.append("\n▼ 검색기를 눌러 종목 목록 확인")

    keyboard, row_btns = [], []
    for key, data in scan_out.items():
        s = _SC[key]
        cnt = len(data.get("results", []))
        label = f"{s['icon']} {s['name']} 검색({cnt}종)"
        row_btns.append(InlineKeyboardButton(
            label, callback_data=f"coin_scan_list:{ts_key}:{key}"))
        if len(row_btns) == 2:
            keyboard.append(row_btns); row_btns = []
    if row_btns:
        keyboard.append(row_btns)

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard))


async def _handle_coin_all(query, symbol: str, ts_key: str = None, back_screener_key: str = None):
    """6개 검색기 전체 분석 → 결과 요약 + 검색기별 버튼"""
    await query.edit_message_text(
        f"⏳ 🪙 <b>{symbol}</b> — 6개 검색기 분석 중...\n약 10~15초 소요됩니다.",
        parse_mode="HTML"
    )

    try:
        _coin_sys_path()
        from run_scan_coin import scan_single, SCREENERS, _signal_label
        import concurrent.futures, asyncio
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            single_out = await asyncio.wait_for(
                loop.run_in_executor(pool, lambda: scan_single(symbol)),
                timeout=60,
            )
    except asyncio.TimeoutError:
        await query.edit_message_text("⏱ 타임아웃 (60초 초과)")
        return
    except Exception as e:
        await query.edit_message_text(f"❌ 오류: {e}")
        return

    if not single_out:
        await query.edit_message_text(f"❌ {symbol}: 데이터 없음 (빗썸 미상장 또는 심볼 오류)")
        return

    passed_keys = [k for k in SCREENERS if single_out.get(k, {}).get("passed")]
    multi_n = len(passed_keys)

    any_row = next((single_out[k]["row"] for k in SCREENERS if single_out.get(k, {}).get("row")), {})
    close   = any_row.get("close") or "—"
    rsi14   = any_row.get("rsi14") or "—"
    rvol    = any_row.get("rvol") or "—"
    chg     = any_row.get("change_pct") or 0

    lines = [f"🪙 <b>{symbol}</b> — 6검색기 전체 분석\n"]
    if close != "—":
        lines.append(f"현재가: {close:,.0f}원  등락: {chg:+.1f}%")
        lines.append(f"RSI14: {rsi14:.1f}  거래량비율: {rvol:.2f}x\n" if isinstance(rsi14, float) else "")

    if multi_n >= 2:
        lines.append(f"🔥 <b>멀티 시그널 ({multi_n}개 검색기 통과!)</b>\n")
    elif multi_n == 1:
        lines.append(f"✅ {multi_n}개 검색기 통과\n")
    else:
        lines.append("❌ 통과 검색기 없음\n")

    for key in SCREENERS:
        s    = SCREENERS[key]
        info = single_out.get(key, {})
        icon_pass = "✅" if info.get("passed") else "❌"
        score     = info.get("score", 0)
        signal    = info.get("signal", "")
        filled    = round(score / 10)
        bar       = "█" * filled + "░" * (10 - filled)
        sig_str   = f"  {signal}" if signal else ""
        lines.append(f"{icon_pass} {s['icon']} <b>{s['name']}</b>  {score}점  [{bar}]{sig_str}")

    lines.append("\n▼ 검색기를 눌러 상세 결과 + 차트 확인")

    keyboard = []
    row_btns = []
    for key in SCREENERS:
        s    = SCREENERS[key]
        info = single_out.get(key, {})
        icon_pass = "✅" if info.get("passed") else "❌"
        score     = info.get("score", 0)
        label     = f"{icon_pass} {s['icon']} {s['name']} {score}점"
        # ts_key/back_screener_key를 coin_chart 콜백에도 실어 보내야
        # 개별 검색기 상세(④단계)에서 "← 전체검색기 결과로"로 돌아왔을 때
        # 이 화면의 "← 검색기 목록으로" 버튼이 다시 살아남
        if ts_key and back_screener_key:
            chart_cb = f"coin_chart:{symbol}:{key}:{ts_key}:{back_screener_key}"
        else:
            chart_cb = f"coin_chart:{symbol}:{key}"
        row_btns.append(InlineKeyboardButton(label, callback_data=chart_cb))
        if len(row_btns) == 2:
            keyboard.append(row_btns)
            row_btns = []
    if row_btns:
        keyboard.append(row_btns)
    # 스캔 목록에서 진입한 경우 뒤로 버튼 추가
    if ts_key and back_screener_key:
        keyboard.append([InlineKeyboardButton(
            "← 검색기 목록으로", callback_data=f"coin_scan_list:{ts_key}:{back_screener_key}")])

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_coin_chart(query, symbol: str, screener_key: str, ts_key: str = None, back_screener_key: str = None):
    """검색기 버튼 클릭 → 해당 검색기 상세 결과 표시 + 기존 차트 HTML 첨부"""
    import glob as _glob, os as _os

    _coin_sys_path()
    from run_scan_coin import SCREENERS

    s = SCREENERS.get(screener_key, {"name": screener_key, "icon": "📊"})

    # 차트 파일: 최신 것 재사용 (중복 스캔 없이)
    charts_dir = COIN_DIR / "charts"
    found = sorted(_glob.glob(str(charts_dir / f"{symbol}_*.html")),
                   key=_os.path.getmtime)
    chart_path = found[-1] if found else None

    if not chart_path:
        await query.edit_message_text(f"⏳ {s['icon']} {s['name']} × {symbol} — 차트 생성 중...")
        try:
            from run_scan_coin import scan_single
            import concurrent.futures, asyncio
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                single_out = await asyncio.wait_for(
                    loop.run_in_executor(pool, lambda: scan_single(symbol)),
                    timeout=60,
                )
            chart_path = single_out.get("_chart_path")
        except Exception as e:
            await query.edit_message_text(f"❌ 차트 생성 실패: {e}")
            return

    # 지표 직접 계산 (검색기 CSV 컬럼 누락 문제 우회)
    from run_scan_coin import scan_single as _scan_single, SCREENERS as _SC
    passed = False
    score  = 0
    signal = ""
    info_row: dict = {}

    try:
        import concurrent.futures, asyncio as _aio
        loop = _aio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            _out = await _aio.wait_for(
                loop.run_in_executor(pool, lambda: _scan_single(symbol)),
                timeout=60,
            )
        _info = _out.get(screener_key, {})
        passed = bool(_info.get("passed"))
        score  = int(_info.get("score") or 0)
        signal = str(_info.get("signal") or "")

        from data_loader_coin import get_daily_ohlcv as _ohlcv
        import sys as _sys2
        if str(COIN_DIR / ".." / "screener") not in _sys2.path:
            _sys2.path.insert(0, str(COIN_DIR / ".." / "screener"))
        import indicators as _ind
        _df = _ohlcv(symbol, count=200)
        if _df is not None and len(_df) >= 30:
            _df = _ind.add_ema(_df, [20, 45, 120, 200])
            _df = _ind.add_bbands(_df, length=20, std=2.0, prefix="BB20")
            _df = _ind.add_rsi(_df, length=14)
            _df = _ind.add_macd_standard(_df)
            _df = _ind.add_rvol(_df, length=20)
            _last = _df.iloc[-1]
            def _g(col):
                v = _last.get(col)
                return round(float(v), 4) if v is not None and __import__("pandas").notna(v) else None
            macd_h = None
            if _g("MACD_STD") and _g("MACD_STD_SIGNAL"):
                macd_h = round(float(_last["MACD_STD"]) - float(_last["MACD_STD_SIGNAL"]), 4)
            _prev = _df.iloc[-2]
            c_now  = float(_last.get("close", 0) or 0)
            c_prev = float(_prev.get("close", 1) or 1)
            chg = round((c_now - c_prev) / c_prev * 100, 2) if c_prev else 0
            info_row = {
                "close":      _g("close"),
                "change_pct": chg,
                "ema20":      _g("EMA_20"),
                "ema45":      _g("EMA_45"),
                "ema120":     _g("EMA_120"),
                "rsi14":      _g("RSI_14"),
                "macd_hist":  macd_h,
                "bb20_pct":   _g("BB20_PCT"),
                "rvol":       _g("RVOL_20"),
            }
        if chart_path is None:
            chart_path = _out.get("_chart_path")
    except Exception as e:
        logger.error(f"_handle_coin_chart 지표 계산 오류: {e}", exc_info=True)

    def _fmt(v):
        if v is None or (isinstance(v, float) and v != v): return "—"
        if isinstance(v, float): return f"{v:.2f}"
        return str(v)

    filled    = round(score / 10)
    bar       = "█" * filled + "░" * (10 - filled)
    pass_icon = "✅ 통과" if passed else "❌ 미통과 (참고)"
    lines = [
        f"{s['icon']} <b>{s['name']}</b>  ×  {symbol}",
        "",
        f"판정: <b>{pass_icon}</b>",
        f"점수: <b>{score}점</b>  [{bar}]",
        f"핵심신호: {signal or '—'}",
        "",
        "◆ 주요 지표",
        f"종가: {_fmt(info_row.get('close'))}  |  등락: {_fmt(info_row.get('change_pct'))}%",
        f"RSI14: {_fmt(info_row.get('rsi14'))}  |  RVOL: {_fmt(info_row.get('rvol'))}x",
        f"EMA20: {_fmt(info_row.get('ema20'))}  |  EMA120: {_fmt(info_row.get('ema120'))}",
        f"MACD히스토: {_fmt(info_row.get('macd_hist'))}  |  BB%B: {_fmt(info_row.get('bb20_pct'))}",
    ]

    # 뒤로 버튼: 스캔 목록에서 진입한 경우 → 해당 검색기 목록으로, 아니면 6검색기 결과로
    if ts_key and ts_key in _coin_scan_cache:
        from run_scan_coin import SCREENERS as _SC_back
        _sb = _SC_back.get(screener_key, {})
        back_label = f"← {_sb.get('icon', '')} {_sb.get('name', screener_key)} 목록으로"
        back_cb    = f"coin_scan_list:{ts_key}:{screener_key}"
    else:
        back_label = "← 전체 검색기 결과로"
        if ts_key and back_screener_key:
            back_cb = f"coin_all:{symbol}:{ts_key}:{back_screener_key}"
        else:
            back_cb = f"coin_all:{symbol}"
    keyboard = [[InlineKeyboardButton(back_label, callback_data=back_cb)]]
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    if chart_path and _os.path.exists(chart_path):
        try:
            with open(chart_path, "rb") as f:
                await query.message.reply_document(
                    document=f,
                    filename=f"{symbol}_{screener_key}.html",
                    caption=(f"📊 {symbol} [{s['name']}] 차트\n"
                             f"★ 매수 타점 / ● 매도 타점\n"
                             f"다운로드 후 브라우저로 열기"),
                )
        except Exception as e:
            logger.error(f"coin chart upload 실패: {e}", exc_info=True)
            await query.message.reply_text(f"⚠️ 차트 파일 전송 실패: {e}")
    else:
        await query.message.reply_text("⚠️ 차트 파일을 찾을 수 없습니다. /coin GRASS 로 다시 분석 후 시도하세요.")


async def _handle_coin_results(query, screener_key: str, time_str: str):
    """코인 스캔 결과 종목 리스트 표시 (신호/점수 있는 것만)"""
    try:
        import json
        from pathlib import Path

        _coin_sys_path()
        from run_scan_coin import SCREENERS

        # 캐시 파일에서 scan_out 로드 (time_str: "HHMM" 형식)
        cache_file = COIN_DIR / "results" / f"_scan_cache_{time_str}.json"
        if not cache_file.exists():
            await query.answer("❌ 결과 데이터를 찾을 수 없습니다")
            return

        with open(cache_file) as f:
            scan_out = json.load(f)

        if screener_key not in scan_out or not scan_out[screener_key]["results"]:
            await query.answer("❌ 해당 검색기 결과가 없습니다")
            return

        results = scan_out[screener_key]["results"]
        s_name = SCREENERS.get(screener_key, {}).get("name", screener_key)

        # 신호/점수 있는 종목만 필터링
        signal_results = [r for r in results if r.get("signal") and "🟢" in str(r.get("signal"))]
        score_results = [r for r in results if r["grade"] == "A"]

        # 메시지 구성
        msg_lines = [f"📊 <b>{s_name}</b> 스캔 결과"]
        msg_lines.append("")

        # 신호 있는 종목
        if signal_results:
            msg_lines.append(f"<b>🟢 신호 ({len(signal_results)}개)</b>")
            for r in signal_results[:5]:
                msg_lines.append(f"  {r['symbol']} {r['score']}점 {r.get('signal', '')}")
            if len(signal_results) > 5:
                msg_lines.append(f"  +{len(signal_results)-5}종 더")
            msg_lines.append("")

        # 점수 높은 종목 (신호 제외)
        score_only = [r for r in score_results if r not in signal_results]
        if score_only:
            msg_lines.append(f"<b>⭐ A급 ({len(score_only)}개)</b>")
            for r in score_only[:5]:
                msg_lines.append(f"  {r['symbol']} {r['score']}점")
            if len(score_only) > 5:
                msg_lines.append(f"  +{len(score_only)-5}종 더")

        # 종목 버튼 구성 (신호 우선)
        button_list = signal_results + score_only
        if button_list:
            msg_lines.append("")
            msg_lines.append("<i>차트 보기: 아래 종목 버튼 클릭</i>")

        msg = "\n".join(msg_lines)

        # 인라인 버튼 (종목별)
        buttons = []
        for r in button_list[:8]:  # 최대 8개
            sym = r["symbol"]
            score = r["score"]
            buttons.append(InlineKeyboardButton(
                f"{sym} {score}점",
                callback_data=f"coin_chart:{sym}:{screener_key}"
            ))

        # 2열 배치
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"코인 결과 표시 실패: {e}")
        await query.answer(f"❌ 오류")


async def callback_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """coin_results__: / coin_all: / coin_chart: / coin_scan_list: / coin_scan_back: 인라인 버튼 통합 핸들러"""
    query = update.callback_query
    await query.answer("결과 파일 준비 중...")
    data = query.data

    if data.startswith("coin_results__"):
        parts = data.split("__")
        if len(parts) >= 3:
            screener_key = parts[1]
            time_str = parts[2]
            await _handle_coin_results(query, screener_key, time_str)
        return

    if data.startswith("coin_scan_list:"):
        _, ts_key, screener_key = data.split(":", 2)
        await _handle_coin_scan_list(query, ts_key, screener_key)
        return

    if data.startswith("coin_scan_back:"):
        ts_key = data.split(":", 1)[1]
        await _handle_coin_scan_back(query, ts_key)
        return

    if data.startswith("coin_all:"):
        parts = data.split(":", 3)
        symbol        = parts[1]
        ts_key_ca     = parts[2] if len(parts) > 2 else None
        back_screener = parts[3] if len(parts) > 3 else None
        await _handle_coin_all(query, symbol, ts_key=ts_key_ca, back_screener_key=back_screener)
        return

    if data.startswith("coin_chart:"):
        # 최대 5파트: coin_chart:{symbol}:{screener_key}:{ts_key}:{back_screener_key}
        parts = data.split(":", 4)
        if len(parts) >= 3:
            symbol            = parts[1]
            screener_key      = parts[2]
            ts_key            = parts[3] if len(parts) >= 4 and parts[3] else None
            back_screener_key = parts[4] if len(parts) == 5 and parts[4] else None
            await _handle_coin_chart(query, symbol, screener_key, ts_key=ts_key, back_screener_key=back_screener_key)
        return
