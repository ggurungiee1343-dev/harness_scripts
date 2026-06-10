"""
stock_indicators.py — 기술적 지표 계산 (순수 pandas/numpy)
V_FINAL 전략의 모든 지표 구현
V1.8 ~ V3.0 통합 버전
"""
import pandas as pd
import numpy as np
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 기본 지표 계산
# ════════════════════════════════════════════════════════════════

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h_l   = df["high"] - df["low"]
    h_pc  = (df["high"] - df["close"].shift(1)).abs()
    l_pc  = (df["low"]  - df["close"].shift(1)).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(period).mean(), tr

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    line = ema_fast - ema_slow
    sig  = _ema(line, signal)
    hist = line - sig
    return line, sig, hist

def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()

def _chop(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h_l   = df["high"] - df["low"]
    h_pc  = (df["high"] - df["close"].shift(1)).abs()
    l_pc  = (df["low"]  - df["close"].shift(1)).abs()
    tr    = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    tr_sum = tr.rolling(period).sum()
    hh     = df["high"].rolling(period).max()
    ll     = df["low"].rolling(period).min()
    return 100 * np.log10(tr_sum / (hh - ll + 1e-10))

def _adx(df: pd.DataFrame, period: int = 14):
    """ADX + PDI + MDI"""
    h_diff = df["high"].diff()
    l_diff = -df["low"].diff()
    pdi_raw = h_diff.where((h_diff > l_diff) & (h_diff > 0), 0.0)
    mdi_raw = l_diff.where((l_diff > h_diff) & (l_diff > 0), 0.0)
    atr14, _ = _atr(df, period)
    pdi = 100 * pdi_raw.rolling(period).mean() / (atr14 + 1e-10)
    mdi = 100 * mdi_raw.rolling(period).mean() / (atr14 + 1e-10)
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-10)
    adx = dx.rolling(period).mean()
    return adx, pdi, mdi


# ════════════════════════════════════════════════════════════════
# 전체 지표 계산 (단일 종목)
# ════════════════════════════════════════════════════════════════

def compute_all_indicators(df: pd.DataFrame,
                           nasdaq_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    V_FINAL 전략에 필요한 모든 지표 계산
    최소 220봉 이상 필요 (EMA200 + 여유)
    """
    if df.empty or len(df) < 220:
        return pd.DataFrame()

    d = df.copy()

    # ── EMA 스택 ──────────────────────────────────────────────
    for p in [3, 5, 15, 20, 45, 50, 60, 120, 150, 200]:
        d[f"ema{p}"] = _ema(d["close"], p)

    # ── SMA ───────────────────────────────────────────────────
    d["sma20"] = _sma(d["close"], 20)

    # ── VWAP50 (50봉 가중 전형가 이동평균) ────────────────────
    typical = (d["high"] + d["low"] + d["close"]) / 3
    d["vwap50"] = (
        (typical * d["volume"]).rolling(50).sum() /
        d["volume"].rolling(50).sum()
    )

    # ── 볼린저 밴드 (20봉, 2.2σ) ──────────────────────────────
    bb_std = d["close"].rolling(20).std()
    d["bb_up"]    = d["ema20"] + 2.2 * bb_std
    d["bb_low"]   = d["ema20"] - 2.2 * bb_std
    d["bb_pct"]   = (d["close"] - d["bb_low"]) / (d["bb_up"] - d["bb_low"] + 1e-10)
    d["bb_width"] = (d["bb_up"] - d["bb_low"]) / (d["ema20"] + 1e-10)
    d["bb_width_ma50"] = d["bb_width"].rolling(50).mean()

    # ── 엔벨로프 (50봉, ±3%) ──────────────────────────────────
    env_mid    = _sma(d["close"], 50)
    d["env_lo"] = env_mid * 0.97
    d["env_up"] = env_mid * 1.03

    # ── RSI(14) ───────────────────────────────────────────────
    d["rsi14"] = _rsi(d["close"], 14)

    # ── MACD(12,26,9) ─────────────────────────────────────────
    d["macd_line"], d["macd_sig"], d["macd_hist"] = _macd(d["close"])

    # ── OBV ───────────────────────────────────────────────────
    d["obv"]       = _obv(d)
    d["obv_ema20"] = _ema(d["obv"], 20)

    # ── ATR ───────────────────────────────────────────────────
    d["atr14"], tr = _atr(d, 14)
    d["atr10"]     = tr.rolling(10).mean()
    d["atr30"]     = tr.rolling(30).mean()

    # ── CHOP Index ────────────────────────────────────────────
    d["chop"] = _chop(d, 14)

    # ── ADX / DMI(14) ─────────────────────────────────────────
    d["adx"], d["pdi"], d["mdi"] = _adx(d, 14)

    # ── 거래량 지표 ───────────────────────────────────────────
    d["vol_ma20"]       = _sma(d["volume"], 20)
    d["dollar_vol"]     = d["close"] * d["volume"]
    d["dollar_vol_ma20"] = _sma(d["dollar_vol"], 20)

    # ── 52주 고/저 ────────────────────────────────────────────
    d["high_52w"] = d["high"].rolling(252).max()
    d["low_52w"]  = d["low"].rolling(252).min()

    # ── RS_LINE vs NASDAQ ─────────────────────────────────────
    if nasdaq_df is not None and not nasdaq_df.empty:
        nas = nasdaq_df["close"].reindex(d.index, method="ffill")
        d["rs_line"]         = d["close"] / (nas + 1e-10)
        d["rs_line_ma50"]    = _sma(d["rs_line"], 50)
        d["rs_line_52w_high"] = d["rs_line"].rolling(252).max()

    return d


# ════════════════════════════════════════════════════════════════
# 시장 필터
# ════════════════════════════════════════════════════════════════

def compute_market_filter(market_data: dict, vix_limit: float = 28.0) -> dict:
    """
    3중 시장 필터:
    1. NASDAQ > EMA50
    2. VIX < vix_limit (기본 28)
    3. 섹터 브레드스: 11개 중 6개 이상 EMA200 위
    """
    from modules.stock_fetcher import SECTOR_ETFS
    det = {}

    # ① NASDAQ 추세
    nasdaq_pass = False
    nasdaq_val  = None
    if "nasdaq" in market_data:
        nas = market_data["nasdaq"]
        ema50 = _ema(nas["close"], 50)
        nasdaq_val  = round(nas["close"].iloc[-1], 2)
        nasdaq_ema  = round(ema50.iloc[-1], 2)
        nasdaq_pass = nasdaq_val > nasdaq_ema
        det["nasdaq_close"] = nasdaq_val
        det["nasdaq_ema50"] = nasdaq_ema
    det["nasdaq_pass"] = nasdaq_pass

    # ② VIX
    vix_pass = False
    vix_val  = None
    if "vix" in market_data:
        vix_val  = round(market_data["vix"]["close"].iloc[-1], 2)
        vix_pass = vix_val < vix_limit
    det["vix_current"]  = vix_val
    det["vix_limit"]    = vix_limit
    det["vix_pass"]     = vix_pass

    # ③ 섹터 브레드스
    sector_above = {}
    for sector, etf in SECTOR_ETFS.items():
        key = f"sector_{etf}"
        if key in market_data:
            df = market_data[key]
            ema200 = _ema(df["close"], 200)
            above  = df["close"].iloc[-1] > ema200.iloc[-1]
            sector_above[etf] = above
    count = sum(sector_above.values())
    breadth_pass = count >= 6
    det["sector_above"]  = sector_above
    det["breadth_count"] = count
    det["breadth_pass"]  = breadth_pass

    # 최종
    all_pass = nasdaq_pass and vix_pass and breadth_pass
    return {"pass": all_pass, "details": det}
