"""
handlers/_stock.py — 주식 분석 텔레그램 핸들러
V_FINAL 전략 인터페이스

명령어:
  /stock TICKER [account] [risk]  — 단일 종목 분석
  /scan                           — 전체 유니버스 스캔
  /market                         — 시장 필터 현황
  /watchlist [add/rm/list] TICKER — 관심종목 관리
  /positions                      — 열린 포지션 확인
  사진 전송                        — 차트 이미지 분석
"""
import base64
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from modules.stock_fetcher       import StockDataFetcher
from modules.stock_scanner       import StockScanner
from modules.stock_indicators    import compute_market_filter
from modules.weakness_miner      import get_weakness_miner

logger = logging.getLogger(__name__)


async def safe_reply(message, text: str, **kwargs):
    """Markdown 파싱 실패 시 plain text 자동 폴백 (결함 #4 방어 — 패키지 독립성 유지 위해 로컬 정의)"""
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


# ── 기본 설정 (텔레그램으로 /stock config로 변경 가능) ─────────
DEFAULT_ACCOUNT = 100_000
DEFAULT_RISK    = 0.01

_fetcher = StockDataFetcher()
_scanner = StockScanner(account_equity=DEFAULT_ACCOUNT, risk_pct=DEFAULT_RISK)


# ════════════════════════════════════════════════════════════════
# 포맷 유틸
# ════════════════════════════════════════════════════════════════

def _bool_icon(v: bool) -> str:
    return "✅" if v else "❌"

def _score_bar(score: float, max_score: float = 7.5) -> str:
    filled = int(round(score / max_score * 10))
    return "█" * filled + "░" * (10 - filled)

def _format_signal(symbol: str, sig: dict, show_position: bool = True) -> str:
    ind  = sig.get("indicators", {})
    sc   = sig.get("score_detail", {})
    pos  = sig.get("position", {})
    mf   = sig.get("market_filter", {})

    # 상태 뱃지
    badges = []
    if sig.get("sepa_full"):   badges.append("📐SEPA")
    if sig.get("is_tight"):    badges.append("🔒TIGHT")
    if sig.get("is_leader"):   badges.append("🚀LEADER")
    if sig.get("fresh_trend"): badges.append("🌱FRESH")
    badge_str = "  ".join(badges) if badges else "—"

    # 매도 경고
    sell_warns = []
    for k, v in sig.get("sell_conditions", {}).items():
        if v:
            labels = {
                "trend_ema50": "EMA50이탈",
                "profit_rsi":  "RSI과매수",
                "profit_bb":   "BB상단돌파",
                "chop_vol":    "CHOP+거래량소멸",
                "macd_dead":   "MACD데드크로스",
            }
            sell_warns.append(labels.get(k, k))

    lines = [
        f"📊 *{symbol}*  ${sig['close']:,.2f}",
        f"",
        f"ALPHA SCORE  {sig['alpha_score']:.1f} / 7.5",
        f"[{_score_bar(sig['alpha_score'])}]",
        f"",
        badge_str,
        f"",
        f"*◆ 레이어별 상세*",
        f"SEPA 구조      {_bool_icon(sig.get('sepa_full'))}",
        f"  가격 정배열  {_bool_icon(sig.get('sepa_price_align'))}",
        f"  EMA200 상향  {_bool_icon(sig.get('sepa_200_slope'))}",
        f"  52주 범위    {_bool_icon(sig.get('sepa_range'))}",
        f"기관 압축 TIGHT {_bool_icon(sig.get('is_tight'))}  {sc.get('tight', 0):.1f}점",
        f"상대강도 LEADER {_bool_icon(sig.get('is_leader'))}  {sc.get('leader', 0):.1f}점",
        f"신선 추세 FRESH {_bool_icon(sig.get('fresh_trend'))}  {sc.get('fresh', 0):.1f}점",
        f"반전 시그널     {_bool_icon(sc.get('reversal', 0) > 0)}  {sc.get('reversal', 0):.1f}점",
        f"거래량 폭발     {_bool_icon(sc.get('volume', 0) > 0)}  {sc.get('volume', 0):.1f}점",
        f"OBV 강도       {_bool_icon(sc.get('obv', 0) > 0)}  {sc.get('obv', 0):.1f}점",
        f"",
        f"*◆ 주요 지표*",
        f"RSI14  {ind.get('rsi14', '—')}  ADX  {ind.get('adx', '—')}",
        f"CHOP   {ind.get('chop', '—')}  BB%  {ind.get('bb_pct', '—')}",
        f"BB폭   {ind.get('bb_width', '—')}  ATR  {ind.get('atr14', '—')}",
        f"거래량배율 {ind.get('vol_ratio', '—')}x  갭  {ind.get('gap_pct', '—')}%",
        f"MACD Hist {ind.get('macd_hist', '—')}",
        f"EMA50  ${ind.get('ema50', '—'):,}  EMA200 ${ind.get('ema200', '—'):,}",
    ]

    if show_position and sig.get("final_buy"):
        lines += [
            f"",
            f"*◆ 포지션 계산*",
            f"진입가   ${pos.get('entry_price', 0):,.2f}",
            f"손절가   ${pos.get('stop_price', 0):,.2f}",
            f"매수량   {pos.get('shares', 0):,}주",
            f"포지션   ${pos.get('position_value', 0):,.0f}",
            f"리스크   ${pos.get('risk_dollars', 0):,.0f}",
            f"ATR배수  {pos.get('atr_mult', 2.0)}x (VIX 연동)",
        ]

    # 시장 필터
    if mf:
        det = mf.get("details", {})
        lines += [
            f"",
            f"*◆ 시장 필터* {'✅' if mf.get('pass') else '🔴'}",
            f"NASDAQ  {_bool_icon(det.get('nasdaq_pass'))}  "
            f"VIX {det.get('vix_current', '—')} {_bool_icon(det.get('vix_pass'))}  "
            f"섹터 {det.get('breadth_count', '—')}/11 {_bool_icon(det.get('breadth_pass'))}",
        ]

    # 최종 판정
    lines.append("")
    if sig.get("final_buy") and (not mf or mf.get("pass", True)):
        lines.append("🟢 *매수 신호!*")
    elif sig.get("sepa_full"):
        lines.append("🟡 SEPA OK — 타이밍 대기 중")
    else:
        lines.append("🔴 조건 미충족")

    if sell_warns:
        lines.append(f"⚠️ 매도 경고: {', '.join(sell_warns)}")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 핸들러
# ════════════════════════════════════════════════════════════════

async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stock TICKER [account] [risk%]"""
    args = context.args or []
    if not args:
        await safe_reply(update.message, 
            "📈 *주식 분석 명령어*\n\n"
            "`/stock NVDA` — 단일 종목 분석\n"
            "`/stock NVDA 50000 0.01` — 계좌 $50K, 1% 리스크\n"
            "`/scan` — 전체 유니버스 스캔\n"
            "`/market` — 시장 현황\n"
            "`/watchlist add AAPL` — 관심종목 추가\n"
            "`/positions` — 열린 포지션",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    symbol  = args[0].upper()
    account = float(args[1]) if len(args) > 1 else DEFAULT_ACCOUNT
    risk    = float(args[2]) if len(args) > 2 else DEFAULT_RISK

    msg = await safe_reply(update.message, f"⏳ {symbol} 분석 중...")

    try:
        sc = StockScanner(account_equity=account, risk_pct=risk)
        sig = sc.analyze_single(symbol)

        if not sig.get("valid"):
            await safe_edit(msg, f"❌ {symbol}: {sig.get('reason', '오류')}")
            return

        text = _format_signal(symbol, sig, show_position=True)
        await safe_edit(msg, text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"/stock 오류: {e}", exc_info=True)
        await get_weakness_miner().record_failure("cmd_stock", str(e))
        await safe_edit(msg, f"❌ 오류: {e}")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/scan 4티어 스캔
    /scan            — 전체 요약 (4티어 개수 + 각 상위 미리보기)
    /scan buy        — 🟢 Tier1 매수 신호 전체
    /scan watch      — 🟡 Tier2 진입 대기 전체 (SEPA+LEADER, 타이밍 없음)
    /scan strong     — 🔵 Tier3 구조 강함 전체 (정배열+ADX≥40, 52W 미달도 포함)
    /scan sepa       — ⬜ Tier4 SEPA 통과 전체
    /scan TICKER     — 단일 종목 분석 (/stock TICKER 동일)
    """
    args = context.args or []
    sub  = args[0].lower() if args else ""

    # ── 티커 리다이렉트 ────────────────────────────────────────
    if sub and sub not in ("buy", "watch", "strong", "sepa"):
        ticker = sub.upper()
        if ticker.isalpha() and 1 <= len(ticker) <= 5:
            context.args = [ticker] + args[1:]
            await cmd_stock(update, context)
            return

    msg = await safe_reply(update.message, "🔍 NASDAQ 스캔 중... (1~3분 소요)")

    try:
        import concurrent.futures, asyncio
        loop = asyncio.get_event_loop()

        # 진행 상황 메시지 주기적 업데이트 (30초마다)
        async def _progress():
            dots = 1
            phases = ["시장 필터 확인 중", "종목 데이터 수집 중", "지표 계산 중", "신호 분석 중"]
            i = 0
            while True:
                await asyncio.sleep(30)
                i = (i + 1) % len(phases)
                dots = (dots % 3) + 1
                try:
                    await safe_edit(msg, f"🔍 {phases[i]}{'.' * dots} (최대 3분)")
                except Exception:
                    pass

        progress_task = asyncio.create_task(_progress())
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = await asyncio.wait_for(
                    loop.run_in_executor(pool, _scanner.run_scan),
                    timeout=210  # 3분 30초 — 초과 시 TimeoutError
                )
        except asyncio.TimeoutError:
            progress_task.cancel()
            await safe_edit(msg, "⏱ 스캔 타임아웃 (3분 30초 초과)\n네트워크가 느리거나 yfinance 서버 문제일 수 있습니다.\n잠시 후 다시 시도해 주세요.")
            return
        finally:
            progress_task.cancel()

        mf  = result["market_filter"]
        det = mf.get("details", {})

        if not mf.get("pass"):
            text = (
                "🔴 *오늘 매매 중단 — 시장 필터 FAIL*\n\n"
                f"NASDAQ {_bool_icon(det.get('nasdaq_pass'))}\n"
                f"VIX {det.get('vix_current','—')} {_bool_icon(det.get('vix_pass'))}\n"
                f"섹터 {det.get('breadth_count','—')}/11 {_bool_icon(det.get('breadth_pass'))}"
            )
            await safe_edit(msg, text, parse_mode=ParseMode.MARKDOWN)
            return

        stats  = result["stats"]
        buys   = result["buy_signals"]
        watch  = result["watch_ready"]
        strong = result["strong_struct"]
        sepa   = result["sepa_list"]

        mkt_line = (
            f"NASDAQ {_bool_icon(det.get('nasdaq_pass'))}  "
            f"VIX {det.get('vix_current','—')} {_bool_icon(det.get('vix_pass'))}  "
            f"섹터 {det.get('breadth_count','—')}/11 {_bool_icon(det.get('breadth_pass'))}"
        )
        header = (
            f"✅ *스캔 완료* `{result['timestamp']}`\n"
            f"스캔 {stats['scanned']}종목\n"
            f"{mkt_line}\n\n"
            f"🟢 매수신호 {len(buys)}개  🟡 진입대기 {len(watch)}개  "
            f"🔵 구조강함 {len(strong)}개  ⬜ SEPA {stats['sepa_pass']}개"
        )

        # ════════════════════════════════════════════════════════
        # /scan buy — 🟢 Tier1 전체
        # ════════════════════════════════════════════════════════
        if sub == "buy":
            out = header + f"\n\n*🟢 매수 신호 {len(buys)}개*\n"
            if not buys:
                out += "현재 없음\n"
            for s in buys:
                sym, close, score = s["symbol"], s["close"], s["alpha_score"]
                badges = ("🔒" if s.get("is_tight") else "") + \
                         ("🚀" if s.get("is_leader") else "") + \
                         ("🌱" if s.get("fresh_trend") else "")
                out += f"• *{sym}*  ${close:,.0f}  {score:.1f}점 {badges}\n"
                out += f"  └ `/stock {sym}`\n"
            await safe_edit(msg, out.rstrip(), parse_mode=ParseMode.MARKDOWN)
            return

        # ════════════════════════════════════════════════════════
        # /scan watch — 🟡 Tier2 전체
        # ════════════════════════════════════════════════════════
        if sub == "watch":
            pages, chunk = [], header + f"\n\n*🟡 진입 대기 {len(watch)}개* (SEPA+LEADER, 타이밍 대기)\n\n"
            if not watch:
                chunk += "현재 없음\n"
            for s in watch:
                sym, close, score = s["symbol"], s["close"], s["alpha_score"]
                ind = s.get("indicators", {})
                f_  = "🌱" if s.get("fresh_trend") else "·"
                line = (f"🟡 *{sym}*  ${close:,.0f}  {score:.1f}점  {f_}LEADER\n"
                        f"   RSI {ind.get('rsi14','—')} | ADX {ind.get('adx','—')} | `/stock {sym}`\n")
                if len(chunk) + len(line) > 3800:
                    pages.append(chunk); chunk = ""
                chunk += line
            if chunk.strip(): pages.append(chunk)
            await _send_pages(msg, update, pages or [header + "\n진입 대기 종목 없음"])
            return

        # ════════════════════════════════════════════════════════
        # /scan strong — 🔵 Tier3 전체 (RDW 같은 종목)
        # ════════════════════════════════════════════════════════
        if sub == "strong":
            pages, chunk = [], header + f"\n\n*🔵 구조 강함 {len(strong)}개* (정배열+ADX≥40, 52W 미달 포함)\n\n"
            if not strong:
                chunk += "현재 없음\n"
            for s in strong:
                sym, close, score = s["symbol"], s["close"], s["alpha_score"]
                ind = s.get("indicators", {})
                adx  = ind.get("adx", 0)
                rsi  = ind.get("rsi14", 0)
                miss = []
                if not s.get("sepa_range"):   miss.append("52W")
                if not s.get("is_leader"):    miss.append("LEADER")
                if not s.get("fundamental_ok", True): miss.append("펀더")
                miss_str = "/".join(miss) if miss else "—"
                line = (f"🔵 *{sym}*  ${close:,.0f}  ADX {adx:.0f}  RSI {rsi:.0f}\n"
                        f"   미달: {miss_str}  점수: {score:.1f}pt  `/stock {sym}`\n")
                if len(chunk) + len(line) > 3800:
                    pages.append(chunk); chunk = ""
                chunk += line
            if chunk.strip(): pages.append(chunk)
            chunk_note = "\n_ADX 내림차순 | 52W 범위 미달이어도 추세 강함_"
            if pages: pages[-1] += chunk_note
            await _send_pages(msg, update, pages or [header + "\n구조 강함 종목 없음"])
            return

        # ════════════════════════════════════════════════════════
        # /scan sepa — ⬜ Tier4 전체
        # ════════════════════════════════════════════════════════
        if sub == "sepa":
            pages, chunk = [], header + f"\n\n*⬜ SEPA 통과 {len(sepa)}개* (알파점수 순)\n\n"
            for s in sepa:
                sym, close, score = s["symbol"], s["close"], s.get("alpha_score", 0)
                t  = "🔒" if s.get("is_tight")    else "·"
                l  = "🚀" if s.get("is_leader")   else "·"
                f_ = "🌱" if s.get("fresh_trend") else "·"
                b  = "🟢" if s.get("final_buy")   else "⬜"
                line = f"{b} *{sym}*  ${close:,.0f}  {score:.1f}점  {t}{l}{f_}\n"
                if len(chunk) + len(line) > 3800:
                    pages.append(chunk); chunk = ""
                chunk += line
            if chunk.strip(): pages.append(chunk)
            if pages: pages[-1] += "\n🔒=TIGHT  🚀=LEADER  🌱=FRESH"
            await _send_pages(msg, update, pages or [header + "\nSEPA 통과 종목 없음"])
            return

        # ════════════════════════════════════════════════════════
        # 기본 /scan — 4티어 요약 + 각 상위 5개 미리보기
        # ════════════════════════════════════════════════════════
        out = header + "\n"

        # 🟢 Tier1
        if buys:
            out += "\n*🟢 매수 신호*\n"
            for s in buys[:5]:
                sym, close, score = s["symbol"], s["close"], s["alpha_score"]
                badges = ("🔒" if s.get("is_tight") else "") + ("🚀" if s.get("is_leader") else "")
                out += f"• *{sym}*  ${close:,.0f}  {score:.1f}점 {badges}  `/stock {sym}`\n"
            if len(buys) > 5: out += f"_+{len(buys)-5}개 → `/scan buy`_\n"
        else:
            out += "\n*🟢 매수 신호*: 없음\n"

        # 🟡 Tier2
        if watch:
            out += f"\n*🟡 진입 대기* (타이밍 오면 진입)\n"
            for s in watch[:5]:
                sym, close, score = s["symbol"], s["close"], s["alpha_score"]
                out += f"• *{sym}*  ${close:,.0f}  {score:.1f}점\n"
            if len(watch) > 5: out += f"_+{len(watch)-5}개 → `/scan watch`_\n"
        else:
            out += "\n*🟡 진입 대기*: 없음\n"

        # 🔵 Tier3
        if strong:
            out += f"\n*🔵 구조 강함* (ADX강, 52W 미달 포함)\n"
            for s in strong[:5]:
                sym, close = s["symbol"], s["close"]
                adx = s.get("indicators", {}).get("adx", 0)
                out += f"• *{sym}*  ${close:,.0f}  ADX {adx:.0f}\n"
            if len(strong) > 5: out += f"_+{len(strong)-5}개 → `/scan strong`_\n"
        else:
            out += "\n*🔵 구조 강함*: 없음\n"

        # ⬜ Tier4 힌트
        out += f"\n⬜ SEPA 통과 {stats['sepa_pass']}개 → `/scan sepa`\n"

        await safe_edit(msg, out.rstrip(), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"/scan 오류: {e}", exc_info=True)
        await get_weakness_miner().record_failure("cmd_scan", str(e))
        await safe_edit(msg, f"❌ 스캔 오류: {e}")


async def _send_pages(msg, update, pages: list):
    """페이지 분할 발송 유틸"""
    await safe_edit(msg, pages[0], parse_mode=ParseMode.MARKDOWN)
    for page in pages[1:]:
        await safe_reply(update.message, page, parse_mode=ParseMode.MARKDOWN)


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/market — 시장 필터 현황"""
    msg = await safe_reply(update.message, "📡 시장 데이터 수집 중...")

    try:
        from modules.stock_fetcher import StockDataFetcher
        fetcher     = StockDataFetcher()
        market_data = fetcher.get_market_context()
        mf          = compute_market_filter(market_data)
        det         = mf["details"]
        sec         = det.get("sector_above", {})

        sector_lines = []
        for etf, above in sec.items():
            sector_lines.append(f"  {etf}  {_bool_icon(above)}")

        lines = [
            f"🌐 *시장 필터 현황*",
            f"종합: {'✅ 매매 가능' if mf['pass'] else '🔴 매매 중단'}",
            f"",
            f"① NASDAQ {det.get('nasdaq_close', '—')}  EMA50 {det.get('nasdaq_ema50', '—')}  {_bool_icon(det.get('nasdaq_pass'))}",
            f"② VIX {det.get('vix_current', '—')}  기준 <{det.get('vix_limit', 28)}  {_bool_icon(det.get('vix_pass'))}",
            f"③ 섹터 브레드스 {det.get('breadth_count', '—')}/11  {_bool_icon(det.get('breadth_pass'))}",
            f"",
            f"*섹터 EMA200 위치*",
        ] + sector_lines

        await safe_edit(msg, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await safe_edit(msg, f"❌ 오류: {e}")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/watchlist [add/rm/list/scan] [TICKER]"""
    args = context.args or []

    if not args or args[0] == "list":
        wl = _fetcher.watchlist_get()
        if not wl:
            await safe_reply(update.message, "관심종목이 없습니다.\n`/watchlist add AAPL`로 추가하세요.")
            return
        lines = ["📋 *관심종목*\n"]
        for w in wl:
            lines.append(f"• *{w['symbol']}*  ({w['added_at']})  {w.get('notes', '')}")
            lines.append(f"  └ `/stock {w['symbol']}`")
        await safe_reply(update.message, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    elif args[0] == "add" and len(args) > 1:
        sym = args[1].upper()
        notes = " ".join(args[2:]) if len(args) > 2 else ""
        _fetcher.watchlist_add(sym, notes)
        await safe_reply(update.message, f"✅ {sym} 관심종목 추가")

    elif args[0] in ("rm", "remove", "del") and len(args) > 1:
        sym = args[1].upper()
        _fetcher.watchlist_remove(sym)
        await safe_reply(update.message, f"🗑 {sym} 관심종목 삭제")

    elif args[0] == "scan":
        msg = await safe_reply(update.message, "🔍 관심종목 스캔 중...")
        try:
            result = _scanner.scan_watchlist()
            buys   = result.get("buy_signals", [])
            if not buys:
                await safe_edit(msg, "관심종목 중 매수 신호 없음")
            else:
                lines = ["*관심종목 매수 신호*\n"]
                for s in buys:
                    lines.append(f"🟢 *{s['symbol']}* ${s['close']:,.2f}  {s['alpha_score']:.1f}점")
                await safe_edit(msg, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await safe_edit(msg, f"❌ 오류: {e}")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/positions — 열린 포지션"""
    pos_list = _fetcher.get_open_positions()
    if not pos_list:
        await safe_reply(update.message, "열린 포지션 없음")
        return

    lines = ["📂 *열린 포지션*\n"]
    for p in pos_list:
        lines += [
            f"*{p['symbol']}*  진입 ${p['entry_price']:,.2f}  {p['shares']}주",
            f"  손절 ${p['stop_price']:,.2f}  ({p['entry_date']})",
            f"  └ `/stock {p['symbol']}`",
            "",
        ]
    await safe_reply(update.message, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ════════════════════════════════════════════════════════════════
# 차트 이미지 분석 (사진 전송 시 자동 실행)
# ════════════════════════════════════════════════════════════════

async def handle_stock_photo(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              llm_func=None):
    """
    사진 전송 → Claude 비전으로 차트 패턴 분석
    V_FINAL 전략 관점에서 해석
    """
    if not update.message or not update.message.photo:
        return

    caption = update.message.caption or ""
    photo   = update.message.photo[-1]  # 최고화질

    msg = await safe_reply(update.message, "📸 차트 분석 중...")

    try:
        # 사진 다운로드
        file = await context.bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        img_b64   = base64.b64encode(bytes(img_bytes)).decode()

        prompt = f"""이 주식 차트를 V_FINAL 전략 기준으로 분석해줘.

{'📝 메모: ' + caption if caption else ''}

*분석 기준 (V_FINAL 전략)*

[SEPA 체크]
- 가격이 EMA50 > EMA150 > EMA200 정배열인가?
- EMA200이 우상향 중인가?
- 52주 고점 대비 몇 % 위치인가?

[기관 품질]
- 볼린저밴드/ATR 압축(TIGHT) 상태인가?
- 나스닥 대비 상대강도(RS) 주도주인가?
- Stage 2 초기/중기/말기 어느 위치인가?

[타이밍]
- 거래량 폭발적 증가 여부
- RSI 위치 (40~70 중립 구간인가?)
- MACD 방향

[종합 의견]
- 현재 단계: Stage 1/2/3/4
- 매수/관망/매도 판단 + 근거
- 진입 시 주의사항 / 리스크

한국어로, 실용적이고 간결하게 답해줘."""

        # LLM 호출 (harness_agent의 vision 기능 활용)
        if llm_func:
            response = await llm_func(prompt, image_b64=img_b64)
        else:
            # fallback: 텍스트만으로 안내
            response = (
                "📊 차트 분석을 위해 종목명을 알려주시면\n"
                "`/stock TICKER`로 지표 데이터와 함께 정밀 분석해드립니다.\n\n"
                f"캡션: {caption or '없음'}"
            )

        await safe_edit(msg, f"📊 차트 분석\n\n{response}")

    except Exception as e:
        logger.error(f"차트 분석 오류: {e}", exc_info=True)
        await get_weakness_miner().record_failure("cmd_chart", str(e))
        await safe_edit(msg, 
            f"⚠️ 차트 분석 중 오류\n\n"
            f"종목명을 캡션에 써서 다시 보내주시거나\n"
            f"`/stock TICKER`로 분석해드릴게요."
        )


async def cmd_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/result TICKER 매수가 매도가 [수량] [메모]
    예: /result CRWD 580 620 5 SEPA진입_목표가도달
    """
    import sqlite3
    from modules.stock_fetcher import CACHE_DB

    args = context.args or []
    if len(args) < 3:
        await safe_reply(update.message, 
            "사용법: `/result TICKER 매수가 매도가 [수량] [메모]`\n"
            "예시: `/result CRWD 580 620 5 SEPA진입`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        symbol = args[0].upper()
        entry  = float(args[1])
        exit_p = float(args[2])
        shares = int(args[3]) if len(args) > 3 and args[3].isdigit() else 0
        notes  = " ".join(args[4:]) if len(args) > 4 else (
                 " ".join(args[3:]) if len(args) > 3 and not args[3].isdigit() else ""
        )

        pnl_pct    = round((exit_p - entry) / entry * 100, 2)
        pnl_dollar = round((exit_p - entry) * shares, 2) if shares else None

        with sqlite3.connect(CACHE_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT, symbol TEXT,
                    entry_price REAL, exit_price REAL, shares INTEGER,
                    pnl_pct REAL, pnl_dollar REAL, notes TEXT
                )
            """)
            conn.execute(
                "INSERT INTO trade_results VALUES (NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?)",
                (symbol, entry, exit_p, shares, pnl_pct, pnl_dollar, notes)
            )

        icon = "🟢" if pnl_pct > 0 else "🔴"
        lines = [
            f"{icon} *{symbol}* 결과 기록 완료",
            f"진입 ${entry:,.2f} → 매도 ${exit_p:,.2f}",
            f"수익률: *{pnl_pct:+.2f}%*" + (f"  (${pnl_dollar:+.0f})" if pnl_dollar else ""),
        ]
        if notes:
            lines.append(f"메모: {notes}")
        lines.append("\n`/backtest` 로 누적 성과 확인")
        await safe_reply(update.message, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await safe_reply(update.message, f"❌ 오류: {e}")


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
# hermes_local.py 등록용 커맨드 맵
# ════════════════════════════════════════════════════════════════
STOCK_COMMANDS = {
    "stock":     cmd_stock,
    "scan":      cmd_scan,
    "market":    cmd_market,
    "watchlist": cmd_watchlist,
    "positions": cmd_positions,
    "result":    cmd_result,
    "backtest":  cmd_backtest,
}
