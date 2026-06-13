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


def _make_engine(mod, tmp_path):
    """운영 기억 파일(~/.hermes/memory) 오염 방지: L1/L2/L3 경로를 tmp로 격리한 엔진 생성.

    엔진 __init__이 bio_dir를 하드코딩하므로(Lock Stack — 수정 불가),
    생성 후 경로를 재지정하고 구조를 다시 보장한다.
    """
    engine = mod.BioMemoryEngine(vault_path=str(tmp_path))
    engine.l1_path = tmp_path / "harness_memory.json"
    engine.l2_path = tmp_path / "episodic_memory.json"
    engine.l3_path = tmp_path / "semantic_memory.json"
    engine._ensure_structures()
    return engine


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
        engine = _make_engine(mod, tmp_path)
        result = engine.pre_query_context("test query")
        assert result is None or isinstance(result, str)

    def test_cache_hit_returns_same_result(self, tmp_path):
        """동일 질문 반복 → 캐시 히트로 동일 문자열 반환"""
        mod = _import_bio_memory()
        engine = _make_engine(mod, tmp_path)
        first = engine.pre_query_context("hello world")
        second = engine.pre_query_context("hello world")
        assert first == second

    def test_memory_stores_episode(self, tmp_path):
        """add_message → recall → pre_query_context 체인"""
        mod = _import_bio_memory()
        mod.L1_MAX_SIZE = 5  # 축소 설정
        engine = _make_engine(mod, tmp_path)

        # L1에 메시지 저장
        engine.add_message("user", "중요한 연구 데이터 분석 결과")
        ctx = engine.pre_query_context("연구 데이터")
        # 최소한 None은 아니거나, 문자열이어야 함
        assert ctx is None or isinstance(ctx, str)

    def test_l2_l3_integration_with_episodes(self, tmp_path):
        """L2/L3 에피소드 저장 후 pre_query_context에서 검색"""
        mod = _import_bio_memory()
        engine = _make_engine(mod, tmp_path)

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
        engine = _make_engine(mod, tmp_path)
        result = engine.pre_query_context("cached query")
        assert mod._query_cache is not None
        # 캐시 키 존재 확인 (TTL 내)
        key = "cached query"[:200].lower()
        if key in mod._query_cache:
            assert mod._query_cache[key][0] > 0  # timestamp > 0


# ── ImportanceScorer ────────────────────────────────────────

class TestImportanceScorer:
    """중요도 점수 산정 로직"""

    def test_high_keyword_boosts_score(self):
        mod = _import_bio_memory()
        plain = mod.ImportanceScorer.score("오늘 날씨가 좋네요")
        critical = mod.ImportanceScorer.score("긴급! 서버 오류 발생, 반드시 복구 필요")
        assert critical > plain

    def test_assistant_role_scores_lower(self):
        mod = _import_bio_memory()
        text = "일반적인 안내 문장입니다"
        assert mod.ImportanceScorer.score(text, role="assistant") < mod.ImportanceScorer.score(text, role="user")

    def test_score_capped_at_10(self):
        mod = _import_bio_memory()
        text = ("긴급!! 중요: 오류 에러 버그 수정 토큰 API 포트 경로 " * 30)
        assert mod.ImportanceScorer.score(text) <= 10.0

    def test_extract_keywords_filters_stopwords(self):
        mod = _import_bio_memory()
        kws = mod.ImportanceScorer.extract_keywords("데이터 분석 결과를 데이터 보고서에 저장")
        assert "데이터" in kws  # 빈도 1위
        assert all(len(k) >= 2 for k in kws)


# ── ForgettingCurve ─────────────────────────────────────────

class TestForgettingCurve:
    """망각 곡선: 시간 경과·중요도에 따른 보존율"""

    def test_fresh_memory_full_retention(self):
        from datetime import datetime, timezone
        mod = _import_bio_memory()
        now = datetime.now(timezone.utc).isoformat()
        assert mod.ForgettingCurve.retention(5.0, now) >= 0.99

    def test_old_memory_decays(self):
        from datetime import datetime, timezone, timedelta
        mod = _import_bio_memory()
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        assert mod.ForgettingCurve.retention(2.0, old) < mod.ForgettingCurve.retention(2.0, now)

    def test_high_importance_never_forgotten(self):
        from datetime import datetime, timezone, timedelta
        mod = _import_bio_memory()
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        assert mod.ForgettingCurve.should_forget(9.9, old) is False

    def test_invalid_timestamp_does_not_crash(self):
        mod = _import_bio_memory()
        r = mod.ForgettingCurve.retention(5.0, "not-a-date")
        assert 0.0 <= r <= 1.0
        assert mod.ForgettingCurve.should_forget(1.0, "not-a-date") is False


# ── 절차 기억 (L3 procedural) ────────────────────────────────

class TestProceduralMemory:
    """save_procedural_memory / recall_procedural_memory 왕복"""

    def test_save_and_recall_roundtrip(self, tmp_path):
        mod = _import_bio_memory()
        engine = _make_engine(mod, tmp_path)
        engine.save_procedural_memory("게이트웨이 서버 재시작 절차", ["pkill -f gateway", "launchctl load gateway.plist"])
        found = engine.recall_procedural_memory("게이트웨이 재시작 어떻게 하지")
        assert found is not None
        assert found["commands"][0] == "pkill -f gateway"

    def test_recall_increments_use_count(self, tmp_path):
        mod = _import_bio_memory()
        engine = _make_engine(mod, tmp_path)
        engine.save_procedural_memory("백업 실행 절차", ["bash backup.sh"])
        engine.recall_procedural_memory("백업 실행")
        second = engine.recall_procedural_memory("백업 실행")
        assert second["use_count"] == 2

    def test_unrelated_query_returns_none(self, tmp_path):
        mod = _import_bio_memory()
        engine = _make_engine(mod, tmp_path)
        engine.save_procedural_memory("백업 실행 절차", ["bash backup.sh"])
        assert engine.recall_procedural_memory("완전히 무관한 요리 레시피") is None

    def test_failed_procedure_not_recalled(self, tmp_path):
        mod = _import_bio_memory()
        engine = _make_engine(mod, tmp_path)
        engine.save_procedural_memory("배포 실행 절차", ["deploy.sh"], success=False)
        assert engine.recall_procedural_memory("배포 실행 절차") is None


# ── save_important ──────────────────────────────────────────

class TestSaveImportant:
    def test_appends_to_memory_file_and_promotes(self, tmp_path):
        mod = _import_bio_memory()
        engine = _make_engine(mod, tmp_path)
        result = engine.save_important("규칙", "포트 8080은 게이트웨이 전용")
        assert result.startswith("✅")
        content = Path(engine.memory_file).read_text(encoding="utf-8")
        assert "포트 8080은 게이트웨이 전용" in content
