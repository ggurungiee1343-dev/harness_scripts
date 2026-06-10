"""
test_hybrid_router.py — HybridRouter.is_sensitive smoke test
=============================================================
- import 검증
- is_sensitive()가 민감/일반 텍스트를 올바르게 구분하는지 확인
"""

import importlib.util
import pytest
from unittest.mock import MagicMock, AsyncMock

MODULE_PATH = "/Users/bluesea/Applications/Mjauto/Scripts/hybrid_router.py"


def _import_router():
    spec = importlib.util.spec_from_file_location("hybrid_router", MODULE_PATH)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Import ──────────────────────────────────────────────────

def test_import():
    """hybrid_router import 성공"""
    mod = _import_router()
    assert hasattr(mod, "HybridRouter")
    assert hasattr(mod, "HybridRouter"), "HybridRouter 클래스 존재"


# ── HybridRouter.is_sensitive ──────────────────────────────

class TestIsSensitive:
    """is_sensitive() 민감도 탐지 로직"""

    @pytest.fixture
    def router(self):
        """기본 HybridRouter 인스턴스"""
        mod = _import_router()
        return mod.HybridRouter(
            local_llm_func=MagicMock(),
            api_llm_func=AsyncMock(),
        )

    # ── 양성 케이스 (민감 → True) ──

    @pytest.mark.parametrize("text", [
        "이건 미발표 연구 데이터입니다",
        "비공개 프로젝트 정보",
        "내 password는 secret123",
        "private repository access",
        "내 연구 결과는 아직 공개 전",
        "우리 논문 초안입니다",
        "SECRET_KEY found in config",
    ])
    def test_sensitive_texts(self, router, text):
        """민감 패턴 포함 텍스트 → True"""
        assert router.is_sensitive(text) is True

    # ── 음성 케이스 (일반 → False) ──

    @pytest.mark.parametrize("text", [
        "오늘 날씨가 좋네요",
        "Python으로 웹 스크래핑하는 방법",
        "안녕하세요, 도움이 필요합니다",
        "Can you write a hello world in Rust?",
        "public github repository",
        "테스트 코드 작성해줘",
        "일반적인 질문입니다",
    ])
    def test_normal_texts(self, router, text):
        """민감 패턴 없는 일반 텍스트 → False"""
        assert router.is_sensitive(text) is False

    # ── 경계 케이스 ──

    def test_empty_string(self, router):
        """빈 문자열 → False"""
        assert router.is_sensitive("") is False

    def test_case_insensitive(self, router):
        """대소문자 무관 탐지"""
        assert router.is_sensitive("PASSWORD=***") is True
        assert router.is_sensitive("My Password is 123") is True

    def test_partial_match_is_not_false_positive(self, router):
        """부분 일치가 false positive를 만들지 않음"""
        # 'private' 패턴이 없으면 False
        assert router.is_sensitive("public repo") is False
        assert router.is_sensitive("hello world") is False

    def test_sensitive_in_mixed_context(self, router):
        """일반 문맥에 민감 단어 혼합 → True"""
        assert router.is_sensitive(
            "날씨 좋음. 비공개 프로젝트 디렉토리 접근 필요"
        ) is True
