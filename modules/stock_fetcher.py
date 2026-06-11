"""
stock_fetcher.py — 주식 데이터 수집 + 캐시 + 유니버스 관리
V_FINAL 전략용 (yfinance 기반, 순수 pandas)
"""
import yfinance as yf
import pandas as pd
import sqlite3
import logging
import json
import time
from pathlib import Path
from datetime import datetime, date

# yfinance 네트워크 타임아웃용 curl_cffi 세션 (10초 초과 시 skip)
try:
    from curl_cffi import requests as _curl_requests
    _YF_SESSION = _curl_requests.Session(timeout=10)
except Exception:
    _YF_SESSION = None  # curl_cffi 없으면 yfinance 기본 세션 사용

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_DB = DATA_DIR / "stock_cache.db"
MARKET_CACHE_FILE = DATA_DIR / "market_context_cache.json"

# 11개 GICS 섹터 ETF (시장 브레드스 측정용)
SECTOR_ETFS = {
    "Technology":              "XLK",
    "Healthcare":              "XLV",
    "Financials":              "XLF",
    "Energy":                  "XLE",
    "Industrials":             "XLI",
    "Consumer Discretionary":  "XLY",
    "Consumer Staples":        "XLP",
    "Utilities":               "XLU",
    "Materials":               "XLB",
    "Real Estate":             "XLRE",
    "Communication Services":  "XLC",
}

# 핵심 유니버스 — 나스닥 메가/대형주 + 각 섹터 주도주
CORE_UNIVERSE = [
    # ── AI / Semiconductor ───────────────────
    "NVDA", "AMD", "AVGO", "QCOM", "AMAT", "LRCX", "KLAC", "MRVL",
    "MU", "TXN", "INTC", "ARM", "SMCI", "MCHP", "ADI", "NXPI",
    # ── Mega Cap Tech ────────────────────────
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA", "ORCL",
    "CRM", "ADBE", "NOW", "WDAY", "SAP",
    # ── Cloud / SaaS ─────────────────────────
    "SNOW", "DDOG", "HUBS", "TEAM", "ZM", "VEEV", "SPLK",
    # ── Cybersecurity ────────────────────────
    "PANW", "CRWD", "FTNT", "ZS", "NET", "S", "OKTA",
    # ── Healthcare / Biotech ─────────────────
    "LLY", "UNH", "ABBV", "MRK", "TMO", "ISRG", "DHR", "ABT",
    "REGN", "VRTX", "MRNA", "GILD", "AMGN", "BIIB", "IDXX",
    # ── Financials ───────────────────────────
    "JPM", "V", "MA", "BAC", "GS", "MS", "BLK", "SCHW",
    "PYPL", "SQ", "COIN", "AFRM",
    # ── Consumer ─────────────────────────────
    "COST", "HD", "NKE", "SBUX", "MCD", "TGT", "LOW",
    "ROST", "TJX", "TSCO",
    # ── Industrial / Defense ─────────────────
    "CAT", "DE", "HON", "RTX", "LMT", "NOC", "GE",
    # ── Energy ───────────────────────────────
    "XOM", "CVX", "SLB", "EOG", "OXY",
    # ── Communication ────────────────────────
    "NFLX", "DIS", "GOOGL", "META",
    # ── Specialty ────────────────────────────
    "SPGI", "MCO", "ICE", "CME", "MSCI",
    "AMT", "PLD", "EQIX",
    "LIN", "APD", "SHW",
    "UBER", "ABNB", "BKNG",
]
# 중복 제거
CORE_UNIVERSE = list(dict.fromkeys(CORE_UNIVERSE))


class StockDataFetcher:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(CACHE_DB) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol          TEXT    NOT NULL,
                    entry_date      TEXT,
                    entry_price     REAL,
                    shares          INTEGER,
                    stop_price      REAL,
                    max_price       REAL,
                    trailing_stop   REAL,
                    status          TEXT    DEFAULT 'open',
                    exit_date       TEXT,
                    exit_price      REAL,
                    pnl_pct         REAL,
                    notes           TEXT
                );
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol      TEXT PRIMARY KEY,
                    added_at    TEXT,
                    notes       TEXT
                );
                CREATE TABLE IF NOT EXISTS scan_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanned_at  TEXT,
                    market_ok   INTEGER,
                    signals_json TEXT
                );
                CREATE TABLE IF NOT EXISTS fundamentals_cache (
                    symbol              TEXT PRIMARY KEY,
                    cached_at           TEXT,
                    eps_growth          REAL,
                    revenue_growth      REAL,
                    profit_margin       REAL,
                    pe_ratio            REAL,
                    days_to_earnings    INTEGER,
                    market_cap          REAL,
                    sector              TEXT
                );
            """)

    # ── 가격 데이터 ────────────────────────────────────────────
    def get_ohlcv(self, symbol: str, period: str = "2y") -> pd.DataFrame:
        """일봉 OHLCV — yf.download() 우선, 실패 시 ticker.history() 폴백, 재시도 2회"""
        for attempt in range(3):
            try:
                # yf.download()는 bulk endpoint라 rate limit에 더 관대함
                df = yf.download(symbol, period=period, auto_adjust=True,
                                 progress=False, multi_level_index=False)
                if df.empty:
                    raise ValueError("empty")
                df.index = pd.to_datetime(df.index).tz_localize(None)
                cols = {c: c.lower() for c in df.columns}
                df = df.rename(columns=cols)
                for col in ["open", "high", "low", "close", "volume"]:
                    if col not in df.columns:
                        raise ValueError(f"missing col {col}")
                return df[["open", "high", "low", "close", "volume"]].dropna()
            except Exception as e:
                err = str(e)
                if "Rate" in err or "429" in err or "Too Many" in err:
                    wait = 30 * (attempt + 1)
                    logger.warning(f"[{symbol}] Rate limit, {wait}s 대기 후 재시도 ({attempt+1}/3)")
                    time.sleep(wait)
                elif attempt < 2:
                    time.sleep(3)
                else:
                    logger.warning(f"[{symbol}] OHLCV 수집 실패: {e}")
                    return pd.DataFrame()
        return pd.DataFrame()

    # ── 시장 컨텍스트 (일별 파일 캐시로 Rate Limit 회피) ──────────
    def _save_market_cache(self, data: dict):
        """DataFrame dict → JSON 파일 캐시 (today 키 포함)"""
        try:
            serialized = {"date": str(date.today())}
            for key, df in data.items():
                serialized[key] = {
                    "index": [str(i) for i in df.index],
                    "data": df.to_dict(orient="list"),
                }
            MARKET_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            MARKET_CACHE_FILE.write_text(json.dumps(serialized))
        except Exception as e:
            logger.warning(f"시장 캐시 저장 실패: {e}")

    def _load_market_cache(self) -> dict:
        """오늘 날짜의 캐시가 있으면 dict로 복원, 없으면 {}"""
        try:
            if not MARKET_CACHE_FILE.exists():
                return {}
            raw = json.loads(MARKET_CACHE_FILE.read_text())
            if raw.get("date") != str(date.today()):
                return {}
            result = {}
            for key, val in raw.items():
                if key == "date":
                    continue
                idx = pd.to_datetime(val["index"])
                df = pd.DataFrame(val["data"], index=idx)
                result[key] = df
            logger.info("시장 데이터 일별 캐시 로드")
            return result
        except Exception as e:
            logger.warning(f"시장 캐시 로드 실패: {e}")
            return {}

    def get_market_context(self) -> dict:
        """시장 필터용 데이터 일괄 수집 (일별 캐시로 Rate Limit 방지)"""
        cached = self._load_market_cache()
        if cached:
            return cached

        data = {}
        # NASDAQ Composite
        df = self.get_ohlcv("^IXIC", period="1y")
        if not df.empty:
            data["nasdaq"] = df
        time.sleep(1)
        # VIX
        df = self.get_ohlcv("^VIX", period="6mo")
        if not df.empty:
            data["vix"] = df
        time.sleep(1)
        # 11개 섹터 ETF
        for sector, etf in SECTOR_ETFS.items():
            df = self.get_ohlcv(etf, period="1y")
            if not df.empty:
                data[f"sector_{etf}"] = df
            time.sleep(0.5)

        if data:
            self._save_market_cache(data)
        return data

    def get_universe(self) -> list:
        return CORE_UNIVERSE.copy()

    # ── 펀더멘털 ───────────────────────────────────────────────
    def get_fundamentals(self, symbol: str) -> dict:
        """EPS성장률·매출성장률·이익률·실적발표일·섹터 (7일 캐시)"""
        with sqlite3.connect(CACHE_DB) as conn:
            row = conn.execute(
                """SELECT eps_growth, revenue_growth, profit_margin, pe_ratio,
                          days_to_earnings, market_cap, sector
                   FROM fundamentals_cache
                   WHERE symbol=? AND cached_at > datetime('now', '-7 days')""",
                (symbol,)
            ).fetchone()
            if row:
                return dict(zip(
                    ["eps_growth","revenue_growth","profit_margin","pe_ratio",
                     "days_to_earnings","market_cap","sector"], row
                ))

        try:
            ticker = yf.Ticker(symbol, session=_YF_SESSION) if _YF_SESSION else yf.Ticker(symbol)
            info   = ticker.info or {}

            # 실적발표일
            days_to_earn = 999
            try:
                cal = ticker.calendar
                if cal is not None:
                    if hasattr(cal, "loc"):           # DataFrame 형태
                        if "Earnings Date" in cal.index:
                            ed = cal.loc["Earnings Date"].iloc[0]
                            if hasattr(ed, "date"):
                                days_to_earn = (ed.date() - datetime.now().date()).days
                    elif isinstance(cal, dict):       # dict 형태
                        ed_list = cal.get("Earnings Date", [])
                        if ed_list:
                            ed = ed_list[0]
                            if hasattr(ed, "date"):
                                days_to_earn = (ed.date() - datetime.now().date()).days
            except Exception:
                pass

            result = {
                "eps_growth":      info.get("earningsGrowth"),
                "revenue_growth":  info.get("revenueGrowth"),
                "profit_margin":   info.get("profitMargins"),
                "pe_ratio":        info.get("trailingPE"),
                "days_to_earnings": days_to_earn,
                "market_cap":      info.get("marketCap"),
                "sector":          info.get("sector", ""),
            }

            with sqlite3.connect(CACHE_DB) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO fundamentals_cache
                       (symbol, cached_at, eps_growth, revenue_growth, profit_margin,
                        pe_ratio, days_to_earnings, market_cap, sector)
                       VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
                    (symbol, result["eps_growth"], result["revenue_growth"],
                     result["profit_margin"], result["pe_ratio"],
                     result["days_to_earnings"], result["market_cap"], result["sector"])
                )
            return result

        except Exception as e:
            logger.warning(f"[{symbol}] 펀더멘털 조회 실패: {e}")
            return {"eps_growth": None, "revenue_growth": None, "profit_margin": None,
                    "pe_ratio": None, "days_to_earnings": 999,
                    "market_cap": None, "sector": ""}

    # ── 포지션 관리 ────────────────────────────────────────────
    def open_position(self, symbol: str, entry_price: float,
                      shares: int, stop_price: float, notes: str = "") -> int:
        with sqlite3.connect(CACHE_DB) as conn:
            cur = conn.execute(
                "INSERT INTO positions (symbol,entry_date,entry_price,shares,stop_price,notes) "
                "VALUES (?,?,?,?,?,?)",
                (symbol, datetime.now().strftime("%Y-%m-%d"), entry_price, shares, stop_price, notes)
            )
            return cur.lastrowid

    def close_position(self, position_id: int, exit_price: float):
        with sqlite3.connect(CACHE_DB) as conn:
            row = conn.execute(
                "SELECT entry_price FROM positions WHERE id=?", (position_id,)
            ).fetchone()
            if row:
                pnl = (exit_price - row[0]) / row[0] * 100
                conn.execute(
                    "UPDATE positions SET status='closed',exit_date=?,exit_price=?,pnl_pct=? WHERE id=?",
                    (datetime.now().strftime("%Y-%m-%d"), exit_price, round(pnl, 2), position_id)
                )

    def get_open_positions(self) -> list:
        with sqlite3.connect(CACHE_DB) as conn:
            # 구버전 DB 호환: 컬럼 없으면 추가
            for col in [("max_price", "REAL"), ("trailing_stop", "REAL")]:
                try:
                    conn.execute(f"ALTER TABLE positions ADD COLUMN {col[0]} {col[1]}")
                except Exception:
                    pass
            rows = conn.execute(
                "SELECT id,symbol,entry_date,entry_price,shares,stop_price,"
                "max_price,trailing_stop,notes "
                "FROM positions WHERE status='open' ORDER BY entry_date DESC"
            ).fetchall()
        return [dict(zip(
            ["id","symbol","entry_date","entry_price","shares","stop_price",
             "max_price","trailing_stop","notes"], r))
            for r in rows]

    def update_trailing_stop(self, symbol: str, current_price: float) -> dict:
        """ATR 기반 트레일링 스탑 업데이트 (고점 추적, 위로만 이동)"""
        with sqlite3.connect(CACHE_DB) as conn:
            pos = conn.execute(
                "SELECT id, entry_price, stop_price FROM positions WHERE symbol=? AND status='open'",
                (symbol,)
            ).fetchone()
            if not pos:
                return {}

            pos_id, entry_price, orig_stop = pos
            orig_stop_dist = max(entry_price - orig_stop, 0.01)

            # max_price 업데이트
            conn.execute(
                "UPDATE positions SET max_price = MAX(COALESCE(max_price, entry_price), ?) WHERE id=?",
                (current_price, pos_id)
            )
            max_price = conn.execute(
                "SELECT max_price FROM positions WHERE id=?", (pos_id,)
            ).fetchone()[0]

            # 트레일링 스탑: 고점 - 원래 손절폭 (한번 올라간 스탑은 내리지 않음)
            new_trail = max_price - orig_stop_dist
            conn.execute(
                "UPDATE positions SET trailing_stop = MAX(COALESCE(trailing_stop, stop_price), ?) WHERE id=?",
                (new_trail, pos_id)
            )

            updated = conn.execute(
                "SELECT max_price, trailing_stop FROM positions WHERE id=?", (pos_id,)
            ).fetchone()

        return {
            "pos_id":        pos_id,
            "entry_price":   entry_price,
            "max_price":     updated[0],
            "trailing_stop": updated[1],
            "current_price": current_price,
            "hit_stop":      current_price < updated[1],
        }

    # ── 관심종목 ───────────────────────────────────────────────
    def watchlist_add(self, symbol: str, notes: str = ""):
        with sqlite3.connect(CACHE_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO watchlist (symbol,added_at,notes) VALUES (?,?,?)",
                (symbol.upper(), datetime.now().strftime("%Y-%m-%d"), notes)
            )

    def watchlist_remove(self, symbol: str):
        with sqlite3.connect(CACHE_DB) as conn:
            conn.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),))

    def watchlist_get(self) -> list:
        with sqlite3.connect(CACHE_DB) as conn:
            rows = conn.execute(
                "SELECT symbol,added_at,notes FROM watchlist ORDER BY added_at DESC"
            ).fetchall()
        return [{"symbol": r[0], "added_at": r[1], "notes": r[2]} for r in rows]
