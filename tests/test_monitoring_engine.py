"""tests/test_monitoring_engine.py — Monitoring Engine 단위 테스트 (v9.2)"""

import pytest
import tempfile
import os
import time

from modules.monitoring_engine import (
    MonitoringEngine, MetricSnapshot,
    WARN_ERROR_RATE, WARN_RESPONSE_TIME, WARN_ACTION_SUCCESS,
)


@pytest.fixture
def mon():
    """임시 DB를 사용하는 MonitoringEngine 인스턴스."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    engine = MonitoringEngine(db_path=db_path)
    yield engine
    os.unlink(db_path)


def _snapshot(action="ask", time_ms=500, err=False, msg=""):
    return MetricSnapshot(
        timestamp=time.time(),
        action=action,
        response_time_ms=time_ms,
        result_length=100,
        error_flag=err,
        error_message=msg,
    )


@pytest.mark.anyio
async def test_record_metric(mon):
    """record_metric() 기본 기록 검증."""
    snap = _snapshot("ask", 500)
    await mon.record_metric(snap)
    stats = await mon.get_action_stats("ask", hours=24)
    assert stats["total_calls"] == 1
    assert stats["avg_response_time_ms"] == 500.0


@pytest.mark.anyio
async def test_record_error_metric(mon):
    """에러 메트릭 기록 검증."""
    snap = _snapshot("web", 2000, err=True, msg="timeout")
    await mon.record_metric(snap)
    stats = await mon.get_action_stats("web", hours=24)
    assert stats["total_calls"] == 1
    assert stats["error_rate"] == 1.0
    assert stats["success_rate"] == 0.0


@pytest.mark.anyio
async def test_get_all_action_stats(mon):
    """get_all_action_stats() 모든 액션 조회."""
    actions = ["ask", "web", "wiki", "tag", "exec", "file"]
    for action in actions:
        await mon.record_metric(_snapshot(action, 500))

    all_stats = await mon.get_all_action_stats(hours=24)
    for action in actions:
        assert action in all_stats
        assert all_stats[action]["total_calls"] == 1


@pytest.mark.anyio
async def test_get_error_rate(mon):
    """get_error_rate() 전체 에러율."""
    for _ in range(8):
        await mon.record_metric(_snapshot("ask", 300, err=False))
    for _ in range(2):
        await mon.record_metric(_snapshot("ask", 300, err=True))

    err_rate = await mon.get_error_rate(hours=24)
    assert err_rate == 20.0  # 2/10 = 20%


@pytest.mark.anyio
async def test_performance_trend(mon):
    """get_performance_trend() 시간대별 트렌드."""
    # 과거 타임스탬프로 기록
    past = time.time() - 4000  # ~1시간 전
    snap1 = MetricSnapshot(timestamp=past, action="ask", response_time_ms=500,
                            result_length=100, error_flag=False)
    snap2 = MetricSnapshot(timestamp=time.time(), action="ask", response_time_ms=900,
                            result_length=100, error_flag=True)
    await mon.record_metric(snap1)
    await mon.record_metric(snap2)

    trend = await mon.get_performance_trend(hours=24, interval_minutes=60)
    assert "labels" in trend
    assert len(trend["labels"]) >= 1
    assert len(trend["response_times"]) >= 1
    assert len(trend["call_counts"]) >= 1


@pytest.mark.anyio
async def test_alert_no_degradation(mon):
    """alert_if_degradation() — 이상 없음."""
    for _ in range(10):
        await mon.record_metric(_snapshot("ask", 200, err=False))
    alert = await mon.alert_if_degradation(hours=24)
    assert alert is None


@pytest.mark.anyio
async def test_alert_high_error_rate(mon):
    """alert_if_degradation() — 높은 에러율 경고."""
    for _ in range(5):
        await mon.record_metric(_snapshot("ask", 200, err=True))
    for _ in range(5):
        await mon.record_metric(_snapshot("ask", 200, err=False))

    alert = await mon.alert_if_degradation(hours=24)
    assert alert is not None
    assert "에러율" in alert


@pytest.mark.anyio
async def test_alert_high_response_time(mon):
    """alert_if_degradation() — 느린 응답시간 경고."""
    await mon.record_metric(_snapshot("ask", WARN_RESPONSE_TIME + 1000, err=False))

    alert = await mon.alert_if_degradation(hours=24)
    assert alert is not None
    assert "응답시간" in alert


@pytest.mark.anyio
async def test_alert_low_success_rate(mon):
    """alert_if_degradation() — 낮은 성공률 경고."""
    for _ in range(7):
        await mon.record_metric(_snapshot("exec", 200, err=True))
    for _ in range(3):
        await mon.record_metric(_snapshot("exec", 200, err=False))

    alert = await mon.alert_if_degradation(hours=24)
    assert alert is not None
    assert "성공률" in alert


@pytest.mark.anyio
async def test_multiple_action_metrics(mon):
    """여러 액션 동시 기록 통계."""
    await mon.record_metric(_snapshot("web", 100, err=False))
    await mon.record_metric(_snapshot("web", 200, err=False))
    await mon.record_metric(_snapshot("ask", 500, err=True))
    await mon.record_metric(_snapshot("ask", 300, err=False))

    web_stats = await mon.get_action_stats("web")
    assert web_stats["total_calls"] == 2
    assert web_stats["avg_response_time_ms"] == 150.0
    assert web_stats["error_rate"] == 0.0

    ask_stats = await mon.get_action_stats("ask")
    assert ask_stats["total_calls"] == 2
    assert ask_stats["error_rate"] == 0.5


@pytest.mark.anyio
async def test_get_summary(mon):
    """get_summary() 요약 조회."""
    await mon.record_metric(_snapshot("web", 300, err=False))
    summary = await mon.get_summary(hours=24)
    assert "error_rate" in summary
    assert "action_stats" in summary
    assert "degradation" in summary
    assert "web" in summary["action_stats"]


@pytest.mark.anyio
async def test_reset_db(mon):
    """reset_db() 초기화."""
    await mon.record_metric(_snapshot("ask", 500))
    assert (await mon.get_action_stats("ask"))["total_calls"] == 1
    await mon.reset_db()
    assert (await mon.get_action_stats("ask"))["total_calls"] == 0
