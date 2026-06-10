"""tests/test_load_balancer.py — Model Load Balancer 단위 테스트 (v9.2)"""

import pytest
import tempfile
import os
import time

from modules.load_balancer import ModelLoadBalancer, ModelMetric


@pytest.fixture
def lb():
    """임시 DB를 사용하는 ModelLoadBalancer 인스턴스."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    balancer = ModelLoadBalancer(db_path=db_path)
    yield balancer
    os.unlink(db_path)


@pytest.mark.anyio
async def test_select_best_model_no_data(lb):
    """데이터 없는 상태에서 select_best_model() — 모든 모델 동등 확률."""
    model = await lb.select_best_model()
    assert model in ("gemma4", "deepseek", "nvidia")


@pytest.mark.anyio
async def test_record_performance(lb):
    """record_model_performance() 기록 검증."""
    await lb.record_model_performance("deepseek", 500, True)
    rankings = await lb.get_model_rankings(hours=24)
    assert len(rankings) == 3  # 모든 모델 항상 표시

    # deepseek 데이터 확인
    ds = [m for m in rankings if m.model_name == "deepseek"][0]
    assert ds.avg_response_time_ms == 500.0
    assert ds.success_rate == 1.0
    assert ds.total_calls == 1


@pytest.mark.anyio
async def test_get_model_rankings_order(lb):
    """get_model_rankings() 응답시간 오름차순 정렬."""
    # nvidia: 느림
    await lb.record_model_performance("nvidia", 3000, True)
    # deepseek: 보통
    await lb.record_model_performance("deepseek", 1500, True)
    # gemma4: 빠름
    await lb.record_model_performance("gemma4", 500, True)

    rankings = await lb.get_model_rankings(hours=24)
    # 오름차순 확인
    times = [m.avg_response_time_ms for m in rankings]
    assert times == sorted(times)


@pytest.mark.anyio
async def test_rebalance_weights(lb):
    """rebalance_weights() 가중치 재조정."""
    await lb.record_model_performance("gemma4", 500, True)
    await lb.record_model_performance("deepseek", 3000, True)
    await lb.record_model_performance("nvidia", 2000, True)

    weights = await lb.rebalance_weights(hours=24)
    assert "gemma4" in weights
    assert "deepseek" in weights
    assert "nvidia" in weights
    # gemma4(빠름) > nvidia(중간) > deepseek(느림)
    assert weights["gemma4"] > weights["nvidia"]
    assert weights["nvidia"] > weights["deepseek"]


@pytest.mark.anyio
async def test_weighted_selection(lb):
    """가중치 기반 선택에서 빠른 모델이 더 자주 선택됨."""
    # gemma4 매우 빠름, deepseek 매우 느림
    for _ in range(20):
        await lb.record_model_performance("gemma4", 100, True)
    for _ in range(20):
        await lb.record_model_performance("deepseek", 5000, True)

    await lb.rebalance_weights(hours=24)

    # 50회 선택 → gemma4가 압도적으로 많아야 함
    selections = {"gemma4": 0, "deepseek": 0, "nvidia": 0}
    for _ in range(50):
        model = await lb.select_best_model()
        selections[model] += 1

    assert selections["gemma4"] > selections["deepseek"]
    assert selections["gemma4"] > selections["nvidia"]


@pytest.mark.anyio
async def test_error_tracking(lb):
    """실패한 모델 성능 저하 반영."""
    # deepseek: 모두 실패
    await lb.record_model_performance("deepseek", 1500, False)
    await lb.record_model_performance("deepseek", 2000, False)
    # gemma4: 모두 성공
    await lb.record_model_performance("gemma4", 1500, True)

    # rebalance 후 deepseek 가중치 낮음
    await lb.rebalance_weights(hours=24)
    weights = await lb.get_current_weights()
    assert weights["gemma4"] > weights["deepseek"]


@pytest.mark.anyio
async def test_mixed_multi_record(lb):
    """혼합 성능 기록 후 순위."""
    await lb.record_model_performance("deepseek", 800, True)
    await lb.record_model_performance("deepseek", 900, True)
    await lb.record_model_performance("gemma4", 400, True)
    await lb.record_model_performance("gemma4", 500, False)
    await lb.record_model_performance("nvidia", 600, True)

    rankings = await lb.get_model_rankings(hours=24)
    ds = [m for m in rankings if m.model_name == "deepseek"][0]
    gm = [m for m in rankings if m.model_name == "gemma4"][0]
    assert gm.total_calls == 2
    assert ds.total_calls == 2


@pytest.mark.anyio
async def test_get_summary(lb):
    """get_summary() 요약."""
    await lb.record_model_performance("gemma4", 500, True)
    summary = await lb.get_summary(hours=24)
    assert "rankings" in summary
    assert "weights" in summary
    assert "last_rebalance" in summary
    assert len(summary["rankings"]) == 3


@pytest.mark.anyio
async def test_get_current_weights(lb):
    """get_current_weights() 기본 가중치."""
    weights = await lb.get_current_weights()
    for m in ("gemma4", "deepseek", "nvidia"):
        assert m in weights
        assert weights[m] == 1.0


@pytest.mark.anyio
async def test_reset_db(lb):
    """reset_db() 초기화."""
    await lb.record_model_performance("gemma4", 500, True)
    assert (await lb.get_model_rankings())[0].total_calls >= 1
    await lb.reset_db()
    assert all(m.total_calls == 0 for m in await lb.get_model_rankings())
