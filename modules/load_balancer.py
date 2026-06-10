"""
load_balancer.py — ModelLoadBalancer v1.0 (Hermes v9.2)
=====================================================================
3개 모델(Gemma4/DeepSeek/NVIDIA) 간 응답시간 기반 자동 로드 밸런싱.

Priority 3 of v9.2 업그레이드:
  - select_best_model(): 최적 모델 선택 (가중치 기반 확률적)
  - record_model_performance(): 모델 성능 기록
  - get_model_rankings(): 모델 성능 순위
  - rebalance_weights(): 가중치 재조정
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

log = logging.getLogger("load_balancer")

MODEL_METRICS_DB = os.path.expanduser("~/.hermes/runtime/model_metrics.db")
SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name      TEXT,
    response_time_ms REAL,
    success         INTEGER,
    timestamp       REAL
);
CREATE INDEX IF NOT EXISTS idx_model_name ON model_performance(model_name);
CREATE INDEX IF NOT EXISTS idx_model_ts   ON model_performance(timestamp);
"""

# 등록된 모델 목록
AVAILABLE_MODELS = ["gemma4", "deepseek", "nvidia"]


@dataclass
class ModelMetric:
    """모델 메트릭 스냅샷."""
    model_name: str
    avg_response_time_ms: float
    success_rate: float       # 0~1
    weight: float = 1.0
    total_calls: int = 0


class ModelLoadBalancer:
    """
    3개 모델(Gemma4/DeepSeek/NVIDIA) 간 Load Balancer.

    알고리즘:
      1. 지난 24시간 평균 응답시간 + 성공률 조회
      2. 가중치 = 1.0 / (avg_response_time * (1.0 - success_rate + 0.01))
      3. 누적 가중치 기반 확률적 선택

    사용법:
        lb = ModelLoadBalancer()
        best = await lb.select_best_model()
        await lb.record_model_performance("gemma4", 850, True)
        rankings = await lb.get_model_rankings()
    """

    def __init__(self, db_path: str = MODEL_METRICS_DB):
        self.db_path = db_path
        self._model_weights: Dict[str, float] = {
            m: 1.0 for m in AVAILABLE_MODELS
        }
        self._last_rebalance = time.time()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    # ── DB 초기화 ───────────────────────────────────────

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SQL_SCHEMA)
        conn.commit()
        conn.close()
        log.info(f"[LoadBalancer] 모델 메트릭 DB 초기화: {self.db_path}")

    # ── 핵심 API ────────────────────────────────────────

    async def select_best_model(self) -> str:
        """
        응답 시간 + 성공률 기반 최적 모델을 선택합니다.

        가중치 계산:
          weight = 1.0 / (avg_response_time_ms * (1.0 - success_rate + 0.01))

        데이터가 없는 모델은 기본 가중치 1.0으로 선택 확률 균등.

        Returns:
            "gemma4", "deepseek", "nvidia" 중 하나
        """
        metrics = await self._get_model_metrics()
        weights = self._calculate_weights(metrics)

        # 가중치 기반 확률적 선택
        model_names = list(weights.keys())
        model_weights = list(weights.values())

        # 가중치 정규화
        total = sum(model_weights)
        if total <= 0:
            # 모든 가중치가 0이면 균등 선택
            return random.choice(AVAILABLE_MODELS)

        probs = [w / total for w in model_weights]

        # 확률적 선택
        selected = random.choices(model_names, weights=probs, k=1)[0]
        log.info(
            f"[LoadBalancer] 모델 선택: {selected} "
            f"(가중치: {dict(zip(model_names, [round(p, 3) for p in probs]))})"
        )
        return selected

    async def record_model_performance(
        self,
        model_name: str,
        response_time_ms: float,
        success: bool,
    ) -> None:
        """
        모델 성능을 SQLite에 기록.

        Args:
            model_name: "gemma4", "deepseek", "nvidia"
            response_time_ms: 응답 시간 (ms)
            success: 성공 여부
        """
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO model_performance
                   (model_name, response_time_ms, success, timestamp)
                   VALUES (?, ?, ?, ?)""",
                (model_name, response_time_ms, int(success), time.time()),
            )
            conn.commit()
        finally:
            conn.close()

        log.debug(
            f"[LoadBalancer] 성능 기록: {model_name} "
            f"{response_time_ms:.0f}ms {'✅' if success else '❌'}"
        )

    async def get_model_rankings(self, hours: int = 24) -> List[ModelMetric]:
        """
        모델 성능 순위 조회 (응답시간 오름차순).

        Returns:
            [ModelMetric(nvidia, 1500ms, 1.0), ModelMetric(deepseek, 2000ms, 0.98), ...]
        """
        metrics = await self._get_model_metrics(hours)
        ranked = sorted(metrics.values(), key=lambda m: m.avg_response_time_ms)
        return ranked

    async def rebalance_weights(self, hours: int = 24) -> Dict[str, float]:
        """
        모델 가중치 재조정. 1시간마다 호출 권장.

        Returns:
            {"gemma4": 0.0025, "deepseek": 0.025, "nvidia": 0.05}
        """
        metrics = await self._get_model_metrics(hours)
        new_weights = self._calculate_weights(metrics)

        self._model_weights = new_weights
        self._last_rebalance = time.time()

        log.info(
            f"[LoadBalancer] 가중치 재조정: "
            f"{', '.join(f'{k}={v:.4f}' for k, v in new_weights.items())}"
        )
        return new_weights

    # ── 내부 메서드 ─────────────────────────────────────

    async def _get_model_metrics(
        self, hours: int = 24
    ) -> Dict[str, ModelMetric]:
        """DB에서 모델 메트릭 조회."""
        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(self.db_path)
        try:
            result: Dict[str, ModelMetric] = {}

            for model in AVAILABLE_MODELS:
                row = conn.execute(
                    """SELECT
                           AVG(response_time_ms),
                           SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*),
                           COUNT(*)
                       FROM model_performance
                       WHERE model_name = ? AND timestamp > ?""",
                    (model, cutoff),
                ).fetchone()

                if row and row[2] and row[2] > 0:
                    result[model] = ModelMetric(
                        model_name=model,
                        avg_response_time_ms=round(row[0], 1) if row[0] else 1000.0,
                        success_rate=round(row[1], 3) if row[1] else 0.0,
                        weight=self._model_weights.get(model, 1.0),
                        total_calls=row[2],
                    )
                else:
                    # 데이터가 없는 모델: 기본값
                    result[model] = ModelMetric(
                        model_name=model,
                        avg_response_time_ms=1000.0,
                        success_rate=0.95,
                        weight=self._model_weights.get(model, 1.0),
                        total_calls=0,
                    )

            return result

        finally:
            conn.close()

    def _calculate_weights(
        self, metrics: Dict[str, ModelMetric]
    ) -> Dict[str, float]:
        """
        가중치 계산 공식:
          weight = 1.0 / (avg_response_time_ms * (1.0 - success_rate + 0.01))

        - 응답시간이 짧을수록 가중치 상승
        - 성공률이 높을수록 가중치 상승
        - 0.01은 zero-division 방지 epsilon
        """
        weights: Dict[str, float] = {}

        for model, metric in metrics.items():
            denom = metric.avg_response_time_ms * (1.0 - metric.success_rate + 0.01)
            if denom <= 0:
                weights[model] = 100.0  # 완벽한 모델 최우선
            else:
                weights[model] = 1.0 / denom

        return weights

    def _weighted_choice(self, weights: Dict[str, float]) -> str:
        """가중치 기반 확률적 선택 (동기식)."""
        model_names = list(weights.keys())
        model_weights = list(weights.values())
        total = sum(model_weights)
        if total <= 0:
            return random.choice(AVAILABLE_MODELS)
        return random.choices(model_names, weights=model_weights, k=1)[0]

    # ── 유틸리티 ────────────────────────────────────────

    async def get_current_weights(self) -> Dict[str, float]:
        """현재 가중치 조회."""
        return dict(self._model_weights)

    async def get_summary(self, hours: int = 24) -> Dict[str, Any]:
        """로드 밸런서 요약."""
        rankings = await self.get_model_rankings(hours)
        return {
            "rankings": [
                {
                    "model": m.model_name,
                    "avg_response_time_ms": m.avg_response_time_ms,
                    "success_rate": m.success_rate,
                    "weight": m.weight,
                    "total_calls": m.total_calls,
                }
                for m in rankings
            ],
            "weights": await self.get_current_weights(),
            "last_rebalance": self._last_rebalance,
            "period_hours": hours,
        }

    async def reset_db(self):
        """테스트용: DB 초기화."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM model_performance")
        conn.commit()
        conn.close()
        log.info("[LoadBalancer] DB 초기화 완료")
