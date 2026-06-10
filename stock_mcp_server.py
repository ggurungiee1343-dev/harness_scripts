"""
stock_mcp_server.py — V_FINAL 주식 분석 MCP 서버
Claude Code에서 직접 주식 분석 도구 사용 가능

사용법 (Claude Code 등록):
  claude mcp add stock-scanner -- python3 /Users/bluesea/Applications/Mjauto/Scripts/stock_mcp_server.py

등록 후 Claude Code에서:
  "NVDA 분석해줘" → analyze_stock 도구 자동 호출
  "오늘 매수 후보 찾아줘" → run_scan 도구 자동 호출
"""
import sys
import os
import json
import asyncio
import logging

# Scripts 경로를 Python path에 추가
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

logging.basicConfig(level=logging.WARNING)  # MCP는 조용히

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("stock-scanner")


# ── 도구 목록 ──────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="analyze_stock",
            description=(
                "단일 종목 V_FINAL 전략 분석. "
                "SEPA 트렌드, IS_TIGHT, IS_LEADER, ALPHA_SCORE, "
                "포지션 사이징(ATR 기반), 매도 조건까지 포함. "
                "Webull에서 차트 보면서 수치 교차 검증에 사용."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "종목 티커 (예: NVDA, CRWD, AAPL)"
                    },
                    "account": {
                        "type": "number",
                        "description": "계좌 금액 USD (기본값: 100000)",
                        "default": 100000
                    },
                    "risk_pct": {
                        "type": "number",
                        "description": "종목당 리스크 비율 (기본값: 0.01 = 1%)",
                        "default": 0.01
                    }
                },
                "required": ["symbol"]
            }
        ),
        types.Tool(
            name="run_scan",
            description=(
                "NASDAQ 유니버스 전체 스캔. 3중 시장 필터(NASDAQ/VIX/섹터) 통과 후 "
                "V_FINAL 조건 만족 종목만 추출. 1~3분 소요. "
                "장 마감 후 매일 실행 추천."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "특정 종목 리스트 (생략 시 전체 유니버스 스캔)"
                    },
                    "account": {
                        "type": "number",
                        "description": "계좌 금액 USD (기본값: 100000)",
                        "default": 100000
                    }
                }
            }
        ),
        types.Tool(
            name="market_status",
            description=(
                "현재 시장 상태 확인. NASDAQ 추세, VIX, 11개 섹터 브레드스. "
                "매매 전 시장 환경 파악용."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="record_result",
            description=(
                "매매 결과 기록. 수식 개선을 위한 피드백 데이터 축적. "
                "결과가 쌓이면 /backtest로 V_FINAL 파라미터 최적화 가능."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "종목 티커"},
                    "entry_price": {"type": "number", "description": "매수가"},
                    "exit_price": {"type": "number", "description": "매도가"},
                    "shares": {"type": "integer", "description": "주식 수"},
                    "notes": {
                        "type": "string",
                        "description": "메모 (예: 'SEPA+TIGHT 진입, 목표가 도달 익절')"
                    }
                },
                "required": ["symbol", "entry_price", "exit_price"]
            }
        ),
        types.Tool(
            name="backtest_summary",
            description=(
                "기록된 매매 결과 통계 분석. "
                "승률, 평균 수익률, 어떤 조건 조합이 실제로 잘 맞았는지 확인. "
                "V_FINAL 수식 개선 근거 제공."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="watchlist",
            description="관심종목 관리 (조회/추가/삭제)",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "remove"],
                        "description": "list=조회, add=추가, remove=삭제"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "종목 티커 (add/remove 시 필요)"
                    },
                    "notes": {
                        "type": "string",
                        "description": "메모 (add 시 선택)"
                    }
                },
                "required": ["action"]
            }
        ),
    ]


# ── 도구 실행 ──────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "analyze_stock":
            result = await asyncio.to_thread(_analyze_stock, arguments)
        elif name == "run_scan":
            result = await asyncio.to_thread(_run_scan, arguments)
        elif name == "market_status":
            result = await asyncio.to_thread(_market_status)
        elif name == "record_result":
            result = await asyncio.to_thread(_record_result, arguments)
        elif name == "backtest_summary":
            result = await asyncio.to_thread(_backtest_summary)
        elif name == "watchlist":
            result = await asyncio.to_thread(_watchlist, arguments)
        else:
            result = f"알 수 없는 도구: {name}"
    except Exception as e:
        result = f"❌ 오류: {e}"

    return [types.TextContent(type="text", text=str(result))]


# ── 내부 실행 함수 ─────────────────────────────────────────────

def _analyze_stock(args: dict) -> str:
    from modules.stock_scanner import StockScanner
    symbol  = args["symbol"].upper()
    account = args.get("account", 100_000)
    risk    = args.get("risk_pct", 0.01)

    scanner = StockScanner(account_equity=account, risk_pct=risk)
    sig     = scanner.analyze_single(symbol)

    if not sig.get("valid"):
        return f"❌ {symbol}: {sig.get('reason', '분석 실패')}"

    return _format_analysis(symbol, sig)


def _run_scan(args: dict) -> str:
    from modules.stock_scanner import StockScanner
    account = args.get("account", 100_000)
    symbols = args.get("symbols")

    scanner = StockScanner(account_equity=account)
    result  = scanner.run_scan(symbols=symbols, verbose=False)

    lines = []
    mf = result["market_filter"]
    mf_icon = "✅" if mf.get("pass") else "🔴"
    det = mf.get("details", {})
    lines.append(f"## 시장 상태 {mf_icon}")
    lines.append(f"NASDAQ {det.get('nasdaq_close','?')} {'✅' if det.get('nasdaq_pass') else '❌'} | "
                 f"VIX {det.get('vix_current','?')} {'✅' if det.get('vix_pass') else '❌'} | "
                 f"섹터 {det.get('breadth_count','?')}/11 {'✅' if det.get('breadth_pass') else '❌'}")

    if not mf.get("pass"):
        lines.append("\n⛔ 시장 필터 미통과 — 신규 매수 보류")
        return "\n".join(lines)

    buys = result["buy_signals"]
    if not buys:
        stats = result["stats"]
        lines.append(f"\n매수 신호 없음 (스캔 {stats['scanned']}종목, SEPA {stats['sepa_pass']}종목)")
        return "\n".join(lines)

    lines.append(f"\n## 🏆 매수 후보 {len(buys)}종목")
    for i, s in enumerate(buys[:10], 1):
        sym   = s["symbol"]
        score = s["alpha_score"]
        bar   = "━" * int(score / 7.5 * 10) + "░" * (10 - int(score / 7.5 * 10))
        badges = []
        if s.get("sepa_full"):   badges.append("SEPA")
        if s.get("is_tight"):    badges.append("TIGHT")
        if s.get("is_leader"):   badges.append("LEADER")
        if s.get("fresh_trend"): badges.append("FRESH")
        pos = s.get("position", {})
        lines.append(
            f"{i}. **{sym}** ${s['close']} | {score:.1f}점 {bar} | "
            f"{' '.join(badges)} | "
            f"진입${pos.get('entry_price','?')} 손절${pos.get('stop_price','?')} {pos.get('shares','?')}주"
        )

    return "\n".join(lines)


def _market_status() -> str:
    from modules.stock_fetcher import StockDataFetcher
    from modules.stock_indicators import compute_market_filter

    fetcher = StockDataFetcher()
    market  = fetcher.get_market_context()
    mf      = compute_market_filter(market)
    det     = mf["details"]

    lines = []
    icon = "✅ 매매 가능" if mf["pass"] else "🔴 매매 보류"
    lines.append(f"## 시장 상태: {icon}")
    lines.append(f"\n**NASDAQ**: {det.get('nasdaq_close','?')} (EMA50: {det.get('nasdaq_ema50','?')}) "
                 f"{'✅' if det.get('nasdaq_pass') else '❌'}")
    lines.append(f"**VIX**: {det.get('vix_current','?')} (기준: {det.get('vix_limit','?')}) "
                 f"{'✅' if det.get('vix_pass') else '❌'}")

    above = det.get("sector_above", {})
    ok    = [k for k, v in above.items() if v]
    no    = [k for k, v in above.items() if not v]
    lines.append(f"\n**섹터 브레드스**: {det.get('breadth_count','?')}/11 "
                 f"{'✅' if det.get('breadth_pass') else '❌'}")
    if ok: lines.append(f"  EMA200 위: {', '.join(ok)}")
    if no: lines.append(f"  EMA200 아래: {', '.join(no)}")

    return "\n".join(lines)


def _record_result(args: dict) -> str:
    import sqlite3
    from modules.stock_fetcher import CACHE_DB

    symbol      = args["symbol"].upper()
    entry       = args["entry_price"]
    exit_price  = args["exit_price"]
    shares      = args.get("shares", 0)
    notes       = args.get("notes", "")
    pnl_pct     = round((exit_price - entry) / entry * 100, 2)
    pnl_dollar  = round((exit_price - entry) * shares, 2) if shares else None

    with sqlite3.connect(CACHE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT,
                symbol TEXT,
                entry_price REAL,
                exit_price REAL,
                shares INTEGER,
                pnl_pct REAL,
                pnl_dollar REAL,
                notes TEXT
            )
        """)
        conn.execute(
            "INSERT INTO trade_results VALUES (NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?)",
            (symbol, entry, exit_price, shares, pnl_pct, pnl_dollar, notes)
        )

    icon = "🟢" if pnl_pct > 0 else "🔴"
    msg  = f"{icon} {symbol} 결과 기록 완료\n"
    msg += f"  진입: ${entry} → 매도: ${exit_price}\n"
    msg += f"  수익률: {pnl_pct:+.2f}%"
    if pnl_dollar is not None:
        msg += f" (${pnl_dollar:+.0f})"
    if notes:
        msg += f"\n  메모: {notes}"
    return msg


def _backtest_summary() -> str:
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
                "SELECT symbol, pnl_pct, pnl_dollar, notes, recorded_at FROM trade_results ORDER BY recorded_at DESC"
            ).fetchall()
    except Exception as e:
        return f"❌ 데이터 조회 실패: {e}"

    if not rows:
        return "📊 기록된 매매 결과 없음\n`record_result` 도구로 결과를 먼저 기록하세요."

    wins   = [r for r in rows if r[1] > 0]
    losses = [r for r in rows if r[1] <= 0]
    total  = len(rows)
    win_r  = len(wins) / total * 100
    avg    = sum(r[1] for r in rows) / total
    avg_w  = sum(r[1] for r in wins)  / len(wins)  if wins   else 0
    avg_l  = sum(r[1] for r in losses)/ len(losses) if losses else 0

    lines = [f"## 📊 V_FINAL 매매 성과 ({total}건)"]
    lines.append(f"승률: {win_r:.1f}% ({len(wins)}승 {len(losses)}패)")
    lines.append(f"평균 수익률: {avg:+.2f}%")
    lines.append(f"평균 이익: {avg_w:+.2f}% | 평균 손실: {avg_l:+.2f}%")
    if wins and losses:
        rr = abs(avg_w / avg_l) if avg_l != 0 else 0
        lines.append(f"리스크:리워드 = 1 : {rr:.2f}")
    lines.append(f"\n### 최근 10건")
    for sym, pnl, dollar, note, ts in rows[:10]:
        icon = "🟢" if pnl > 0 else "🔴"
        d_str = f" (${dollar:+.0f})" if dollar else ""
        lines.append(f"{icon} {sym} {pnl:+.2f}%{d_str} | {ts[:10]}")
        if note:
            lines.append(f"   └ {note}")

    return "\n".join(lines)


def _watchlist(args: dict) -> str:
    from modules.stock_fetcher import StockDataFetcher
    fetcher = StockDataFetcher()
    action  = args["action"]

    if action == "list":
        wl = fetcher.watchlist_get()
        if not wl:
            return "관심종목 없음. action='add'로 추가하세요."
        lines = ["## 관심종목"]
        for w in wl:
            lines.append(f"• {w['symbol']} | {w.get('added_at','')[:10]} | {w.get('notes','')}")
        return "\n".join(lines)

    elif action == "add":
        sym = args.get("symbol", "").upper()
        if not sym:
            return "symbol 파라미터 필요"
        fetcher.watchlist_add(sym, notes=args.get("notes", ""))
        return f"✅ {sym} 관심종목 추가 완료"

    elif action == "remove":
        sym = args.get("symbol", "").upper()
        if not sym:
            return "symbol 파라미터 필요"
        fetcher.watchlist_remove(sym)
        return f"🗑️ {sym} 관심종목 삭제"

    return "action은 list/add/remove 중 하나"


def _format_analysis(symbol: str, sig: dict) -> str:
    mf  = sig.get("market_filter", {})
    pos = sig.get("position", {})
    ind = sig.get("indicators", {})
    sc  = sig.get("score_detail", {})

    market_icon = "✅" if mf.get("pass") else "🔴"
    buy_icon    = "🟢 매수 신호" if sig.get("final_buy") else "⏳ 대기"
    score       = sig.get("alpha_score", 0)
    bar         = "━" * int(score / 7.5 * 10) + "░" * (10 - int(score / 7.5 * 10))

    lines = [f"## {symbol} ${sig['close']} | {buy_icon}"]
    lines.append(f"시장 {market_icon} | ALPHA {score:.1f}/7.5 {bar}")

    # Layer 1 SEPA
    lines.append("\n**[Layer 1] SEPA 트렌드**")
    lines.append(f"  정배열: {'✅' if sig.get('sepa_price_align') else '❌'} | "
                 f"EMA200 우상향: {'✅' if sig.get('sepa_200_slope') else '❌'} | "
                 f"52W 위치: {'✅' if sig.get('sepa_range') else '❌'}")

    # Layer 2
    lines.append("\n**[Layer 2] 기관 품질**")
    lines.append(f"  TIGHT: {'✅' if sig.get('is_tight') else '❌'} | "
                 f"LEADER: {'✅' if sig.get('is_leader') else '❌'} | "
                 f"FRESH: {'✅' if sig.get('fresh_trend') else '❌'} | "
                 f"SAFE: {'✅' if sig.get('safe_margin') else '❌'}")

    # Layer 3 점수
    lines.append("\n**[Layer 3] 점수 분해**")
    score_items = [
        ("TIGHT", sc.get("tight", 0), 1.5),
        ("LEADER", sc.get("leader", 0), 1.5),
        ("FRESH", sc.get("fresh", 0), 1.0),
        ("REVERSAL", sc.get("reversal", 0), 1.5),
        ("VOLUME", sc.get("volume", 0), 1.5),
        ("OBV", sc.get("obv", 0), 0.5),
    ]
    for name, got, max_v in score_items:
        icon = "✅" if got > 0 else "☐"
        lines.append(f"  {icon} {name}: {got:.1f}/{max_v:.1f}")

    # 포지션 사이징
    if pos:
        lines.append("\n**포지션 사이징** (ATR 기반)")
        lines.append(f"  진입: ${pos.get('entry_price','?')} | "
                     f"손절: ${pos.get('stop_price','?')} | "
                     f"수량: {pos.get('shares','?')}주")
        lines.append(f"  투자금: ${pos.get('position_value',0):,.0f} | "
                     f"리스크: ${pos.get('risk_dollars',0):,.0f} | "
                     f"ATR배수: {pos.get('atr_mult','?')}x")

    # 주요 지표
    lines.append("\n**지표 스냅샷**")
    lines.append(f"  RSI14: {ind.get('rsi14','?')} | ADX: {ind.get('adx','?')} | "
                 f"CHOP: {ind.get('chop','?')} | BB%: {ind.get('bb_pct','?')}")
    lines.append(f"  EMA50: {ind.get('ema50','?')} | EMA200: {ind.get('ema200','?')} | "
                 f"VWAP50: {ind.get('vwap50','?')}")
    lines.append(f"  거래량 비율: {ind.get('vol_ratio','?')}x | 갭: {ind.get('gap_pct','?')}%")

    # 매도 경보
    sells = sig.get("sell_conditions", {})
    active_sells = [k for k, v in sells.items() if v]
    if active_sells:
        lines.append(f"\n⚠️ **매도 경보**: {', '.join(active_sells)}")

    return "\n".join(lines)


# ── 메인 ──────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
