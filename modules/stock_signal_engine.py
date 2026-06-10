"""
stock_signal_engine.py — V_FINAL 매수/매도 신호 엔진
V1.8 ~ V3.0 전략 병합 + 개선
"""
import pandas as pd
import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_signals(df: pd.DataFrame,
                    account_equity: float = 100_000,
                    risk_pct: float = 0.01,
                    vix_current: Optional[float] = None,
                    fundamentals: Optional[dict] = None,
                    top_sectors: Optional[list] = None) -> dict:
    """
    V_FINAL v1.3 전략 — 전체 신호 계산

    ┌──────────────────────────────────────────────────────────────┐
    │ Layer 1 : SEPA 트렌드 템플릿 (Stage 2 구조 확인)             │
    │ Layer 2A: 기관 품질 (IS_TIGHT / IS_LEADER / FRESH_TREND)     │
    │ Layer 2B: 펀더멘털 필터 (EPS >15% / 매출 >10% / 흑자)  [NEW]│
    │           실적발표 필터 (days_to_earnings > 10)         [NEW]│
    │ Layer 3 : ALPHA_SCORE 타이밍 점수 (최대 8.5점)          [NEW]│
    │           REVERSAL 부분 점수화 (macd/ema/rsi 독립 3개)  [NEW]│
    │           섹터 RS 보너스 +0.5 (상위 4개 섹터)           [NEW]│
    │ Layer 4 : FINAL_BUY = L1 AND L2A AND L2B AND L3 ≥ 3.0       │
    │ Layer 5 : 매도 조건 5개 + 트레일링 스탑                 [NEW]│
    └──────────────────────────────────────────────────────────────┘
    """
    if df.empty or len(df) < 220:
        return {"valid": False, "reason": "데이터 부족"}

    cur  = df.iloc[-1]   # 현재봉
    prev = df.iloc[-2]   # 전봉

    # ════════════════════════════════════════════
    # [Layer 1] SEPA 트렌드 템플릿
    # ════════════════════════════════════════════

    # 이평선 정배열: 종가 > EMA50 > EMA150 > EMA200
    sepa_price_align = (
        cur["close"]  > cur["ema50"]  and
        cur["ema50"]  > cur["ema150"] and
        cur["ema150"] > cur["ema200"]
    )

    # EMA200 우상향: 20일 전보다 현재가 높아야 함 (V2.3 수정 버전)
    ema200_20d = df["ema200"].iloc[-21] if len(df) >= 21 else df["ema200"].iloc[0]
    sepa_200_slope = cur["ema200"] > ema200_20d

    # 52주 범위 필터: 저가 +30% 이상, 고가 -25% 이내
    sepa_range = (
        cur["close"] >= cur["low_52w"]  * 1.30 and
        cur["close"] >= cur["high_52w"] * 0.75
    )

    sepa_full = sepa_price_align and sepa_200_slope and sepa_range

    # 구조 부분 통과: 정배열 + EMA200 우상향 (52W 범위 미달이어도)
    # → Tier3 "구조 강함" 판정용 (RDW 같은 케이스)
    sepa_structure = sepa_price_align and sepa_200_slope

    # ════════════════════════════════════════════
    # [Layer 2] 기관 품질 지표 (V3.0 핵심)
    # ════════════════════════════════════════════

    # IS_TIGHT: 에너지 축적 (기관이 물량 조용히 쌓는 구간)
    # ATR 압축 + 볼린저 밴드 축소
    is_tight = (
        cur["atr10"]     < cur["atr30"]        * 0.85 and
        cur["bb_width"]  < cur["bb_width_ma50"] * 0.80
    )

    # IS_LEADER: 나스닥 대비 상대강도 주도주
    is_leader = False
    if "rs_line" in df.columns:
        rs_52w_high = cur["rs_line_52w_high"]
        rs_ma_20d   = df["rs_line_ma50"].iloc[-21] if len(df) >= 21 else df["rs_line_ma50"].iloc[0]
        is_leader = (
            cur["rs_line"]      >= rs_52w_high * 0.90 and  # RS 52주 고점의 90% 이상 (기존 97%→90%)
            cur["rs_line_ma50"] >  rs_ma_20d               # RS MA 상승 중
        )

    # FRESH_TREND: EMA200이 60일/120일 전보다 높아야 함 (V3.0)
    ema200_60d  = df["ema200"].iloc[-61]  if len(df) >= 61  else None
    ema200_120d = df["ema200"].iloc[-121] if len(df) >= 121 else None
    fresh_trend = bool(
        ema200_60d  is not None and
        ema200_120d is not None and
        cur["ema200"] > ema200_60d and
        cur["ema200"] > ema200_120d
    )

    # SAFE_MARGIN: 버블 과열 차단 (3년 저가 대비 8배 이상 상승 종목 제외)
    low_3y = df["low"].rolling(750).min().iloc[-1] if len(df) >= 750 else df["low"].min()
    safe_margin = cur["close"] < low_3y * 8.0

    # ════════════════════════════════════════════
    # [Layer 2B] 펀더멘털 + 실적발표 필터 (v1.3)
    # ════════════════════════════════════════════
    fundamental_ok = True   # 데이터 없으면 기본 통과 (보수적 설계)
    earnings_safe  = True
    fund_sector    = ""
    fund_detail    = {}

    if fundamentals:
        eg  = fundamentals.get("eps_growth")
        rg  = fundamentals.get("revenue_growth")
        pm  = fundamentals.get("profit_margin")
        dte = fundamentals.get("days_to_earnings", 999)
        fund_sector = fundamentals.get("sector", "")

        # 세 값이 모두 존재할 때만 필터 적용 (None이면 통과)
        if eg is not None and rg is not None and pm is not None:
            fundamental_ok = (
                eg > 0.15 and   # EPS 성장률 >15%
                rg > 0.10 and   # 매출 성장률 >10%
                pm > 0          # 흑자 기업
            )

        # 실적발표 10일 이내 → 매수 보류 (이미 발표 완료된 경우는 OK)
        earnings_safe = (dte > 10 or dte < 0)

        fund_detail = {
            "eps_growth":      round(eg * 100, 1) if eg is not None else None,
            "revenue_growth":  round(rg * 100, 1) if rg is not None else None,
            "profit_margin":   round(pm * 100, 1) if pm is not None else None,
            "days_to_earnings": dte,
            "sector":          fund_sector,
        }

    # ════════════════════════════════════════════
    # [Layer 3] ALPHA_SCORE (최대 8.5점, v1.3)
    # ════════════════════════════════════════════
    score = {}

    # ① IS_TIGHT +1.5
    score["tight"] = 1.5 if is_tight else 0.0

    # ② IS_LEADER +1.5
    score["leader"] = 1.5 if is_leader else 0.0

    # ③ FRESH_TREND +1.0
    score["fresh"] = 1.0 if fresh_trend else 0.0

    # ④ REVERSAL 부분 점수화 (v1.3) — 최대 1.5점
    #    기존: 5조건 동시 충족 → 1.5 (거의 0점)
    #    신규: macd_cross(0.75) + ema_cross(0.50) + rsi_zone(0.25) 독립 누적
    macd_golden = (cur["macd_line"]  > cur["macd_sig"] and
                   prev["macd_line"] <= prev["macd_sig"])
    cross_ema3  = cur["close"] > cur["ema3"]   and prev["close"] <= prev["ema3"]
    cross_ema15 = cur["close"] > cur["ema15"]  and prev["close"] <= prev["ema15"]
    rsi_ok      = 45 <= cur["rsi14"] <= 68

    score["macd_cross"] = 0.75 if macd_golden else 0.0
    score["ema_cross"]  = 0.50 if (cross_ema3 or cross_ema15) else 0.0
    score["rsi_zone"]   = 0.25 if (rsi_ok and cur["close"] > cur.get("vwap50", 0)) else 0.0

    # ⑤ SCORE_VOLUME +1.5 (달러 거래대금 기준 — V1.8 주식수 기준 개선)
    #    달러 거래대금 폭발(1.8배↑) + 갭업(0.5%↑)
    dollar_surge = cur["dollar_vol"] > cur["dollar_vol_ma20"] * 1.8
    gap_pct = (cur["open"] - prev["close"]) / (prev["close"] + 1e-10) * 100
    gap_up   = gap_pct >= 0.5
    if dollar_surge and gap_up:
        score["volume"] = 1.5
    elif dollar_surge:
        score["volume"] = 0.75
    else:
        score["volume"] = 0.0

    # ⑥ OBV +0.5
    score["obv"] = 0.5 if cur["obv"] > cur["obv_ema20"] else 0.0

    # ⑦ 섹터 RS 보너스 +0.5 (v1.3) — 상위 4개 섹터에 속하면 가산
    if top_sectors and fund_sector and fund_sector in top_sectors:
        score["sector_bonus"] = 0.5
    else:
        score["sector_bonus"] = 0.0

    # ⑧ ADX 추세 강도 보너스 +0.5 (v1.4)
    #    ADX: 방향성 지표 (추세 없음<20 / 보통20~40 / 강함40~60 / 극강60↑)
    #    ADX ≥ 60: +0.5 (극강 추세 — 진입 후 지속 확률 높음)
    #    ADX ≥ 40: +0.25 (강한 추세)
    adx_val = cur.get("adx", 0)
    if adx_val >= 60:
        score["adx_bonus"] = 0.5
    elif adx_val >= 40:
        score["adx_bonus"] = 0.25
    else:
        score["adx_bonus"] = 0.0

    alpha_score = round(sum(score.values()), 2)

    # ════════════════════════════════════════════
    # [Layer 4] FINAL_BUY  (v1.3)
    # ════════════════════════════════════════════
    # v1.2: IS_TIGHT 필수 게이트 제외, IS_LEADER 0.90, SCORE≥3.0
    # v1.3: + 펀더멘털 필터 + 실적발표 안전 필터 추가
    final_buy = (
        sepa_full        and
        is_leader        and
        safe_margin      and
        fundamental_ok   and   # EPS>15% / 매출>10% / 흑자 (데이터 없으면 통과)
        earnings_safe    and   # 실적발표 10일 이내 제외
        alpha_score >= 3.0
    )

    # ════════════════════════════════════════════
    # 포지션 사이징 (V3.0 ATR 기반 리스크 표준화)
    # VIX 연동 동적 배수: DYNAMIC_ATR_MULT
    # ════════════════════════════════════════════
    if vix_current is not None:
        if   vix_current < 20: atr_mult = 1.5
        elif vix_current < 25: atr_mult = 2.0
        else:                  atr_mult = 2.5
    else:
        atr_mult = 2.0

    atr_stop    = cur["atr14"] * atr_mult
    entry_stop  = cur["close"] - atr_stop
    account_risk = account_equity * risk_pct
    shares       = max(1, int(account_risk / (atr_stop + 1e-10)))

    # ════════════════════════════════════════════
    # [Layer 5] 매도 조건 (5개 독립 게이트 + 트레일링 스탑)
    # ════════════════════════════════════════════
    # 트레일링 스탑: 포지션 보유 시 외부에서 전달 (fundamentals dict 재활용)
    trailing_stop_price = None
    if fundamentals:
        trailing_stop_price = fundamentals.get("trailing_stop")

    sells = {
        # ① EMA50 이탈 (추세 훼손)
        "trend_ema50":  cur["close"] < cur["ema50"] * 0.99,

        # ② RSI 과매수 (분할 매도 신호)
        "profit_rsi":   cur["rsi14"] > 80,

        # ③ BB 상단 돌파 (과열 익절)
        "profit_bb":    cur["close"] > cur["bb_up"] * 1.05,

        # ④ 모멘텀 훼손 (CHOP 과열 + 거래량 소멸)
        "chop_vol":     cur["chop"] > 65 and cur["volume"] < cur["vol_ma20"] * 0.8,

        # ⑤ MACD 데드크로스 (방향 전환)
        "macd_dead":    (
            cur["macd_line"]  < cur["macd_sig"] and
            prev["macd_line"] >= prev["macd_sig"]
        ),

        # ⑥ 트레일링 스탑 이탈 (v1.3: 포지션 보유 시만 활성)
        "trailing_stop": (
            trailing_stop_price is not None and
            cur["close"] < trailing_stop_price
        ),
    }

    return {
        "valid":       True,
        "close":       round(cur["close"], 2),
        "final_buy":   final_buy,
        "alpha_score": alpha_score,
        "score_detail": score,

        # Layer 1
        "sepa_full":        sepa_full,
        "sepa_structure":   sepa_structure,   # 정배열+EMA우상향 (52W 무관)
        "sepa_price_align": sepa_price_align,
        "sepa_200_slope":   sepa_200_slope,
        "sepa_range":       sepa_range,

        # Layer 2A
        "is_tight":    is_tight,
        "is_leader":   is_leader,
        "fresh_trend": fresh_trend,
        "safe_margin": safe_margin,

        # Layer 2B (v1.3)
        "fundamental_ok": fundamental_ok,
        "earnings_safe":  earnings_safe,
        "fundamentals":   fund_detail,

        # 매도
        "sell_conditions": sells,
        "any_sell":        any(sells.values()),

        # 포지션 사이징
        "position": {
            "entry_price":    round(cur["close"], 2),
            "stop_price":     round(entry_stop, 2),
            "shares":         shares,
            "position_value": round(cur["close"] * shares, 0),
            "risk_dollars":   round(atr_stop * shares, 0),
            "atr_mult":       atr_mult,
        },

        # 주요 지표 스냅샷
        "indicators": {
            "rsi14":      round(cur["rsi14"], 1),
            "adx":        round(cur["adx"],   1),
            "chop":       round(cur["chop"],  1),
            "bb_pct":     round(cur["bb_pct"], 2),
            "bb_width":   round(cur["bb_width"], 3),
            "atr14":      round(cur["atr14"], 2),
            "macd_hist":  round(cur["macd_hist"], 3),
            "vol_ratio":  round(cur["volume"] / (cur["vol_ma20"] + 1e-10), 2),
            "gap_pct":    round(gap_pct, 2),
            "vwap50":     round(cur["vwap50"], 2),
            "ema50":      round(cur["ema50"], 2),
            "ema200":     round(cur["ema200"], 2),
        },
    }
