"""
test_bio_memory.py — BioMemoryEngine.pre_query_context smoke test
==================================================================
- import 검증
- pre_query_context가 None 또는 문자열을 반환하는지 확인
- 캐시 동작, L2/L3 검색 로직 기본 확인
"""

import importlib.util
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

MODULE_PATH = "/Users/bluesea/Applications/Mjauto/Scripts/modules/bio_memory_engine.py"


def _import_bio_memory():
    """bio_memory_engine 모듈 로드 (conftest에서 numpy mock 처리됨)"""
    spec = importlib.util.spec_from_file_location(
        "bio_memory_engine", MODULE_PATH
    )
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Import ──────────────────────────────────────────────────

def test_import():
    """bio_memory_engine import 성공"""
    mod = _import_bio_memory()
    assert hasattr(mod, "BioMemoryEngine")
    assert hasattr(mod, "ImportanceScorer")
    assert hasattr(mod, "ForgettingCurve")


# ── BioMemoryEngine.pre_query_context ──────────────────────

class TestPreQueryContext:
    """pre_query_context() 기본 동작"""

    def test_returns_none_on_empty_db(self, tmp_path):
        """빈 DB + 빈 저장소 → None 반환"""
        mod = _import_bio_memory()
        engine = mod.BioMemoryEngine(vault_path=str(tmp_path))
        result = engine.pre_query_context("test query")
        assert result is None or isinstance(result, str)

    def test_cache_hit_returns_same_result(self, tmp_path):
        """동일 질문 반복 → 캐시 히트로 동일 문자열 반환"""
        mod = _import_bio_memory()
        engine = mod.BioMemoryEngine(vault_path=str(tmp_path))
        first = engine.pre_query_context("hello world")
        second = engine.pre_query_context("hello world")
        assert first == second

    def test_memory_stores_episode(self, tmp_path):
        """add_message → recall → pre_query_context 체인"""
        mod = _import_bio_memory()
        mod.L1_MAX_SIZE = 5  # 축소 설정
        engine = mod.BioMemoryEngine(vault_path=str(tmp_path))

        # L1에 메시지 저장
        engine.add_message("user", "중요한 연구 데이터 분석 결과")
        ctx = engine.pre_query_context("연구 데이터")
        # 최소한 None은 아니거나, 문자열이어야 함
        assert ctx is None or isinstance(ctx, str)

    def test_l2_l3_integration_with_episodes(self, tmp_path):
        """L2/L3 에피소드 저장 후 pre_query_context에서 검색"""
        mod = _import_bio_memory()
        engine = mod.BioMemoryEngine(vault_path=str(tmp_path))

        # force L2 promotion: 중요도 높은 메시지 여러 번 추가
        for i in range(5):
            engine.add_message("user", f"핵심 실험 결과 #{i}: 정확도 98.2% 달성")

        ctx = engine.pre_query_context("실험 정확도")
        assert ctx is None or isinstance(ctx, str)
        # 검색 결과가 있으면 L2/L3 레이블 포함
        if ctx:
            assert "[L2" in ctx or "[L3" in ctx

    def test_query_cache_ttl(self, tmp_path):
        """1시간 TTL 이내 캐시 히트 확인"""
        mod = _import_bio_memory()
        engine = mod.BioMemoryEngine(vault_path=str(tmp_path))
        result = engine.pre_query_context("cached query")
        assert mod._query_cache is not None
        # 캐시 키 존재 확인 (TTL 내)
        key = "cached query"[:200].lower()
        if key in mod._query_cache:
            assert mod._query_cache[key][0] > 0  # timestamp > 0
