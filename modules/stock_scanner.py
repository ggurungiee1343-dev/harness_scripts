"""
stock_scanner.py — NASDAQ 전체 스캔 오케스트레이터
V_FINAL 전략 적용
"""
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from modules.stock_fetcher    import StockDataFetcher, CACHE_DB
from modules.stock_indicators import compute_all_indicators, compute_market_filter
from modules.stock_signal_engine import compute_signals

logger = logging.getLogger(__name__)


class StockScanner:
    def __init__(self, account_equity: float = 100_000, risk_pct: float = 0.01):
        self.fetcher        = StockDataFetcher()
        self.account_equity = account_equity
        self.risk_pct       = risk_pct

    # ── 전체 스캔 ─────────────────────────────────────────────
    def run_scan(self, symbols: Optional[list] = None, verbose: bool = True) -> dict:
        """
        NASDAQ 유니버스 스캔
        Returns: {market_filter, buy_signals, sell_watch, stats}
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        result = {
            "timestamp":    ts,
            "market_filter": {},
            # ── 4티어 분류 ──────────────────────────────────
            "buy_signals":    [],  # 🟢 Tier1: FINAL_BUY
            "watch_ready":    [],  # 🟡 Tier2: SEPA+LEADER+SCORE≥2.0 (타이밍 대기)
            "strong_struct":  [],  # 🔵 Tier3: 정배열+EMA우상향+ADX≥40 (구조강함)
            "sepa_list":      [],  # ⬜ Tier4: SEPA 통과 전체
            "sell_watch":     [],
            "stats": {"scanned": 0, "sepa_pass": 0, "strong": 0, "watch": 0, "errors": 0},
        }

        # ① 시장 필터
        if verbose: logger.info("시장 데이터 수집 중...")
        market_data  = self.fetcher.get_market_context()
        mf           = compute_market_filter(market_data)
        result["market_filter"] = mf

        vix_current = mf["details"].get("vix_current")
        nasdaq_df   = market_data.get("nasdaq")

        if not mf["pass"]:
            if verbose: logger.info("⛔ 시장 필터 FAIL → 오늘 매매 중단")
            self._save_scan_log(ts, False, result)
            return result

        # ② 섹터 RS 상위 4개 계산 (v1.3)
        top_sectors = self._get_top_sectors(market_data)
        if verbose: logger.info(f"섹터 RS 상위: {top_sectors}")

        # ③ 종목 스캔
        universe = symbols or self.fetcher.get_universe()
        if verbose: logger.info(f"스캔 대상: {len(universe)}종목")

        for sym in universe:
            try:
                df = self.fetcher.get_ohlcv(sym)
                if df.empty or len(df) < 220:
                    continue

                df_ind = compute_all_indicators(df, nasdaq_df)
                if df_ind.empty:
                    continue

                # 펀더멘털 (7일 캐시) + 트레일링 스탑 (포지션 보유 시)
                fund = self.fetcher.get_fundamentals(sym)
                trail_info = self.fetcher.update_trailing_stop(sym, df_ind["close"].iloc[-1])
                if trail_info:
                    fund["trailing_stop"] = trail_info.get("trailing_stop")

                sig = compute_signals(
                    df_ind,
                    account_equity=self.account_equity,
                    risk_pct=self.risk_pct,
                    vix_current=vix_current,
                    fundamentals=fund,
                    top_sectors=top_sectors,
                )
                if not sig.get("valid"):
                    continue

                result["stats"]["scanned"] += 1
                adx = sig.get("indicators", {}).get("adx", 0)

                # ── Tier 분류 ──────────────────────────────
                if sig["final_buy"]:
                    # 🟢 Tier1: 최종 매수 신호
                    result["buy_signals"].append({"symbol": sym, **sig})

                if sig.get("sepa_full"):
                    result["stats"]["sepa_pass"] += 1
                    result["sepa_list"].append({"symbol": sym, **sig})

                    if not sig["final_buy"] and sig.get("is_leader") and sig["alpha_score"] >= 2.0:
                        # 🟡 Tier2: SEPA 완전통과 + LEADER + 점수 2.0↑ (타이밍만 없음)
                        result["watch_ready"].append({"symbol": sym, **sig})
                        result["stats"]["watch"] += 1

                elif sig.get("sepa_structure") and adx >= 40 and not sig.get("any_sell"):
                    # 🔵 Tier3: 정배열+EMA우상향+ADX≥40 (52W 미달이어도 구조 강함)
                    result["strong_struct"].append({"symbol": sym, **sig})
                    result["stats"]["strong"] += 1

                if sig["any_sell"]:
                    result["sell_watch"].append({"symbol": sym, **sig})

            except Exception as e:
                result["stats"]["errors"] += 1
                logger.warning(f"[{sym}] 처리 오류: {e}")

        # 점수 내림차순 정렬
        result["buy_signals"].sort(key=lambda x: x["alpha_score"], reverse=True)
        result["watch_ready"].sort(key=lambda x: x["alpha_score"], reverse=True)
        result["strong_struct"].sort(
            key=lambda x: x.get("indicators", {}).get("adx", 0), reverse=True)
        result["sepa_list"].sort(key=lambda x: x["alpha_score"], reverse=True)

        self._save_scan_log(ts, True, result)
        if verbose:
            logger.info(
                f"스캔 완료 | 🟢매수: {len(result['buy_signals'])}개 "
                f"| 🟡대기: {len(result['watch_ready'])}개 "
                f"| 🔵구조강함: {len(result['strong_struct'])}개 "
                f"| ⬜SEPA: {result['stats']['sepa_pass']}개"
            )
        return result

    # ── 단일 종목 분석 ────────────────────────────────────────
    def analyze_single(self, symbol: str) -> dict:
        """단일 종목 상세 분석"""
        market_data = self.fetcher.get_market_context()
        mf          = compute_market_filter(market_data)
        nasdaq_df   = market_data.get("nasdaq")
        vix_current = mf["details"].get("vix_current")

        df     = self.fetcher.get_ohlcv(symbol)
        if df.empty:
            return {"valid": False, "reason": f"{symbol} 데이터 없음"}

        df_ind = compute_all_indicators(df, nasdaq_df)
        if df_ind.empty:
            return {"valid": False, "reason": "데이터 부족 (200일 미만)"}

        fund = self.fetcher.get_fundamentals(symbol)
        trail_info = self.fetcher.update_trailing_stop(symbol, df_ind["close"].iloc[-1])
        if trail_info:
            fund["trailing_stop"] = trail_info.get("trailing_stop")

        top_sectors = self._get_top_sectors(market_data)

        sig = compute_signals(
            df_ind,
            account_equity=self.account_equity,
            risk_pct=self.risk_pct,
            vix_current=vix_current,
            fundamentals=fund,
            top_sectors=top_sectors,
        )
        sig["market_filter"] = mf
        sig["symbol"]        = symbol
        return sig

    # ── 관심종목 스캔 ─────────────────────────────────────────
    def scan_watchlist(self) -> dict:
        wl = self.fetcher.watchlist_get()
        syms = [w["symbol"] for w in wl]
        if not syms:
            return {"buy_signals": [], "sell_watch": [], "market_filter": {}}
        return self.run_scan(symbols=syms)

    # ── 섹터 RS 순위 ──────────────────────────────────────────
    def _get_top_sectors(self, market_data: dict) -> list:
        """섹터 ETF의 현재가/EMA50 비율 기준 상위 4개 섹터명 반환"""
        from modules.stock_fetcher import SECTOR_ETFS
        scores = []
        for sector, etf in SECTOR_ETFS.items():
            df = market_data.get(f"sector_{etf}")
            if df is None or len(df) < 50:
                continue
            ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
            price = df["close"].iloc[-1]
            rs = price / ema50 if ema50 > 0 else 0.0
            scores.append((sector, rs))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:4]]

    # ── 로그 저장 ─────────────────────────────────────────────
    def _save_scan_log(self, ts: str, market_ok: bool, result: dict):
        try:
            summary = {
                "buy_count":  len(result.get("buy_signals", [])),
                "sell_count": len(result.get("sell_watch",  [])),
                "stats":      result.get("stats", {}),
                "market":     result.get("market_filter", {}).get("details", {}),
                "top_buys":   [
                    {"symbol": s["symbol"], "score": s["alpha_score"]}
                    for s in result.get("buy_signals", [])[:5]
                ],
            }
            with sqlite3.connect(CACHE_DB) as conn:
                conn.execute(
                    "INSERT INTO scan_log (scanned_at, market_ok, signals_json) VALUES (?,?,?)",
                    (ts, int(market_ok), json.dumps(summary))
                )
        except Exception as e:
            logger.warning(f"스캔 로그 저장 실패: {e}")
