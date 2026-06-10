import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from modules.core_reducer import (
    AgentContext, SourceChannel, DecisionAgent, ExecutionAgent,
    KnowledgeAgent, HermesCoreReducer
)

def test_agent_context_hash_serialization():
    ctx = AgentContext(
        user_query="Hello World",
        source_channel=SourceChannel.CLI,
        hot_context="Some context info",
        pems_score=0.7,
        user_id="user123"
    )
    # Verify hash is consistent
    h1 = ctx.get_hash()
    h2 = ctx.get_hash()
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 is 64 hex chars
    
    # Verify serialization roundtrip
    serialized = ctx.to_dict()
    assert serialized["user_query"] == "Hello World"
    assert serialized["source_channel"] == "cli"
    
    deserialized = AgentContext.from_dict(serialized)
    assert deserialized.user_query == ctx.user_query
    assert deserialized.source_channel == ctx.source_channel
    assert deserialized.hot_context == ctx.hot_context
    assert deserialized.pems_score == ctx.pems_score
    assert deserialized.user_id == ctx.user_id
    assert deserialized.get_hash() == h1

@pytest.mark.anyio
async def test_decision_agent_rules():
    agent = DecisionAgent()
    
    # Test keyword web routing
    ctx = AgentContext(user_query="최신 뉴스 검색해줘", source_channel=SourceChannel.CLI)
    decision = await agent.decide(ctx)
    assert decision["action"] == "web"
    assert decision["confidence"] == 0.9

    # Test keyword wiki routing
    ctx = AgentContext(user_query="위키: 파이썬 문법", source_channel=SourceChannel.CLI)
    decision = await agent.decide(ctx)
    assert decision["action"] == "wiki"
    assert decision["confidence"] == 0.9

    # Test default fallback to ask
    ctx = AgentContext(user_query="그냥 평범한 질문", source_channel=SourceChannel.CLI)
    decision = await agent.decide(ctx)
    assert decision["action"] == "ask"
    assert decision["confidence"] == 0.5

@pytest.mark.anyio
async def test_decision_agent_llm():
    # Mock LLM response
    mock_llm = AsyncMock(return_value='{"action": "exec", "query": "ls -la", "confidence": 0.95, "reasoning": "directory list requested"}')
    agent = DecisionAgent(llm=mock_llm)
    
    ctx = AgentContext(user_query="디렉토리를 확인해줘", source_channel=SourceChannel.CLI)
    decision = await agent.decide(ctx)
    
    assert decision["action"] == "exec"
    assert decision["query"] == "ls -la"
    assert decision["confidence"] == 0.95
    mock_llm.assert_called_once()

@pytest.mark.anyio
async def test_execution_agent_dispatch():
    exec_agent = ExecutionAgent()
    
    # Mock handlers.cmd_ask_logic
    with patch("handlers._memory.cmd_ask_logic", new_callable=AsyncMock) as mock_ask:
        mock_ask.return_value = "CoVe Verified Answer"
        res = await exec_agent.execute({"action": "ask", "query": "What is AI?"})
        assert res["success"] is True
        assert res["result"] == "CoVe Verified Answer"
        mock_ask.assert_called_once_with("What is AI?")

    # Mock handlers.cmd_tag_logic
    with patch("handlers._approval.cmd_tag_logic", new_callable=AsyncMock) as mock_tag:
        mock_tag.return_value = "Tag action result"
        res = await exec_agent.execute({"action": "tag", "query": "pending"})
        assert res["success"] is True
        assert res["result"] == "Tag action result"
        mock_tag.assert_called_once_with("pending")

@pytest.mark.anyio
async def test_knowledge_agent_refine():
    agent = KnowledgeAgent()
    ctx = AgentContext(
        user_query="Test query",
        source_channel=SourceChannel.CLI,
        pems_score=0.5
    )
    
    # Test success refinement
    refined_success = await agent.refine(ctx, {"success": True, "action": "ask", "result": "Good response"})
    assert refined_success.pems_score == 0.6  # pems_score increased by 0.1
    assert refined_success.execution_state == "DONE"
    assert "[ask] Good response" in refined_success.hot_context

    # Test failure refinement
    refined_fail = await agent.refine(ctx, {"success": False, "action": "web", "error": "Network Timeout", "result": ""})
    assert refined_fail.pems_score == 0.4  # pems_score decreased by 0.1
    assert refined_fail.execution_state == "ERROR"
    assert "Network Timeout" in refined_fail.rejected_buffer

@pytest.mark.anyio
async def test_hermes_core_reducer_pipeline():
    """전체 파이프라인 통합 테스트 (v9.1.3 _refine_result 지원)."""
    import tempfile, os
    from unittest.mock import AsyncMock, patch

    # 격리된 DB 경로 — 이전 실행 캐시 영향 방지
    tmp_db = tempfile.mktemp(suffix=".db")
    try:
        # LLM은 두 번 호출됨: 1) 결정, 2) 결과 정제
        mock_llm = AsyncMock()
        mock_llm.side_effect = [
            '{"action": "ask", "query": "hello", "confidence": 0.9}',  # 결정
            "Hello user!",  # 정제 (ask는 _refine_result를 거침)
        ]

        reducer = HermesCoreReducer(llm=mock_llm, db_path=tmp_db)

        ctx = AgentContext(
            user_query="hello",
            source_channel=SourceChannel.CLI
        )

        with patch("handlers._memory.cmd_ask_logic", new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = "Hello user!"
            result = await reducer.reduce(ctx)
            assert result == "Hello user!"
            mock_ask.assert_called_once_with("hello")
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        if os.path.exists(tmp_db + "-wal"):
            os.remove(tmp_db + "-wal")
        if os.path.exists(tmp_db + "-shm"):
            os.remove(tmp_db + "-shm")
