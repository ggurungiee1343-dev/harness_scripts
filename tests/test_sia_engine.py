"""tests/test_sia_engine.py — Self-Improving Agent 단위 테스트 (v9.2)"""

import pytest
from unittest.mock import patch
import tempfile
import os

from modules.sia_engine import SelfImprovingAgent, FeedbackEntry


@pytest.fixture
def sia():
    """임시 DB를 사용하는 SelfImprovingAgent 인스턴스."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    agent = SelfImprovingAgent(db_path=db_path)
    yield agent
    os.unlink(db_path)


@pytest.mark.anyio
async def test_record_feedback(sia):
    """record_feedback() 기본 동작 검증."""
    fid = await sia.record_feedback(
        query="테스트 질문",
        action="ask",
        result="테스트 결과",
        rating=4,
    )
    assert isinstance(fid, int)
    assert fid > 0

    stats = await sia.get_stats()
    assert stats["total_feedback"] == 1
    assert stats["average_rating"] == 4.0


@pytest.mark.anyio
async def test_record_feedback_invalid_rating(sia):
    """rating 범위 검증 (1~5)."""
    with pytest.raises(ValueError):
        await sia.record_feedback(query="?", action="web", result="", rating=0)
    with pytest.raises(ValueError):
        await sia.record_feedback(query="?", action="web", result="", rating=6)


@pytest.mark.anyio
async def test_multiple_feedback(sia):
    """여러 피드백 기록 후 평균 평점 검증."""
    await sia.record_feedback(query="q1", action="web", result="r1", rating=5)
    await sia.record_feedback(query="q2", action="web", result="r2", rating=3)
    await sia.record_feedback(query="q3", action="ask", result="r3", rating=2)

    stats = await sia.get_stats(hours=24)
    assert stats["total_feedback"] == 3
    assert stats["average_rating"] > 3.0
    assert stats["average_rating"] < 3.7


@pytest.mark.anyio
async def test_analyze_trends(sia):
    """analyze_trends() 액션별 평점 분석."""
    await sia.record_feedback(query="q1", action="web", result="r1", rating=5)
    await sia.record_feedback(query="q2", action="web", result="r2", rating=4)
    await sia.record_feedback(query="q3", action="ask", result="r3", rating=2)

    trends = await sia.analyze_trends(hours=24)
    assert "web" in trends
    assert "ask" in trends
    assert trends["web"] == 4.5
    assert trends["ask"] == 2.0


@pytest.mark.anyio
async def test_get_low_performers(sia):
    """get_low_performers() 저성능 액션 감지."""
    # web: 충분히 낮음
    for i in range(3):
        await sia.record_feedback(query=f"q{i}", action="web", result="r", rating=2)
    # ask: 높음
    await sia.record_feedback(query="good", action="ask", result="r", rating=5)

    low = await sia.get_low_performers(threshold=3.0, min_samples=3, hours=24)
    assert "web" in low
    assert "ask" not in low


@pytest.mark.anyio
async def test_get_low_performers_min_samples(sia):
    """min_samples 미만이면 저성능 감지 안 됨."""
    await sia.record_feedback(query="q", action="ask", result="r", rating=1)

    low = await sia.get_low_performers(threshold=3.0, min_samples=3, hours=24)
    assert low == []


@pytest.mark.anyio
async def test_get_recent_feedback(sia):
    """get_recent_feedback() 최신 피드백 조회."""
    await sia.record_feedback(query="latest", action="web", result="ok", rating=5)
    recent = await sia.get_recent_feedback(limit=1)
    assert len(recent) == 1
    assert recent[0]["query"] == "latest"
    assert recent[0]["action"] == "web"
    assert recent[0]["rating"] == 5


@pytest.mark.anyio
async def test_suggest_improvements_no_data(sia):
    """데이터 없는 액션의 suggest_improvements()."""
    suggestion = await sia.suggest_improvements("unknown")
    assert "없습니다" in suggestion


@pytest.mark.anyio
async def test_suggest_improvements_with_data(sia):
    """저평가 피드백 있는 액션의 suggest_improvements()."""
    for _ in range(3):
        await sia.record_feedback(query="bad result", action="ask", result="wrong", rating=1)

    suggestion = await sia.suggest_improvements("ask")
    assert suggestion is not None
    assert len(suggestion) > 0


@pytest.mark.anyio
async def test_suggest_improvements_with_llm(sia):
    """LLM 콜백 기반 개선 제안."""
    async def mock_llm(prompt):
        return "개선 제안: 정확도 향상을 위해 pre-filter 도입"

    sia.llm = mock_llm

    for _ in range(3):
        await sia.record_feedback(query="bad", action="web", result="wrong", rating=1)

    suggestion = await sia.suggest_improvements("web")
    assert "개선 제안" in suggestion


@pytest.mark.anyio
async def test_reset_db(sia):
    """reset_db() 테스트."""
    await sia.record_feedback(query="q", action="web", result="r", rating=5)
    stats_pre = await sia.get_stats()
    assert stats_pre["total_feedback"] == 1

    await sia.reset_db()
    stats_post = await sia.get_stats()
    assert stats_post["total_feedback"] == 0


@pytest.mark.anyio
async def test_different_actions_trends(sia):
    """다양한 액션별 트렌드 분석."""
    for action in ["web", "ask", "wiki", "tag", "exec"]:
        await sia.record_feedback(query=action, action=action, result="test", rating=4)

    trends = await sia.analyze_trends(hours=24)
    assert set(trends.keys()) == {"web", "ask", "wiki", "tag", "exec"}
    for v in trends.values():
        assert v == 4.0
