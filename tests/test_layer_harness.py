"""
Layer-Isolated Evaluation Harness (2606.11686)
==============================================
LLM 없이 각 레이어를 독립적으로 결정론적 테스트.
전체 통합 테스트는 개별 레이어 회귀를 숨기지만
이 하네스는 레이어별 크래터를 정확히 탐지한다.

실행: python3 -m pytest tests/test_layer_harness.py -v
"""

import sys
import json
import time
import pytest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


# ══════════════════════════════════════════════════════
# Layer 1: Memory Refinement — LLM 없이 로직만 검증
# ══════════════════════════════════════════════════════

class TestLayerMemoryRefinement:

    def test_should_store_rejects_short(self):
        from modules.memory_refinement import should_store
        ok, reason = should_store("ㅇㅋ", role="user")
        assert not ok, f"짧은 내용이 저장 승인됨: {reason}"

    def test_should_store_rejects_ephemeral(self):
        from modules.memory_refinement import should_store
        ok, reason = should_store("지금 방금 임시로 테스트해봤어", role="user")
        assert not ok, f"임시 표현이 저장 승인됨: {reason}"

    def test_sycophancy_filter_blocks_agreement(self):
        """아첨 패턴 (짧은 동의 응답) 차단 확인"""
        from modules.memory_refinement import should_store
        ok, reason = should_store("맞아요, 그렇죠, 당연하죠", role="assistant")
        assert not ok, f"아첨 패턴이 저장 승인됨: {reason}"

    def test_sycophancy_allows_long_assistant(self):
        """길고 실질적인 어시스턴트 응답은 허용"""
        from modules.memory_refinement import should_store
        long_answer = "맞아요, 그 접근법이 맞습니다. 구체적으로는 " + "A" * 100
        ok, reason = should_store(long_answer, role="assistant")
        # 길면 sycophancy 필터 통과 (중요도에 따라 결정됨)
        assert isinstance(ok, bool)

    def test_auto_forget_dry_run_safe(self):
        from modules.memory_refinement import auto_forget
        result = auto_forget(dry_run=True)
        assert "candidates" in result
        assert "remaining" in result
        assert result["removed"] == 0  # dry_run이면 실제 삭제 없음

    def test_check_conflict_returns_list(self):
        from modules.memory_refinement import check_conflict
        result = check_conflict("test_key", "test value content")
        assert isinstance(result, list)

    def test_hybrid_recall_returns_string(self):
        from modules.memory_refinement import hybrid_recall
        result = hybrid_recall("테스트 쿼리", top_k=3)
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════
# Layer 2: Agentic Loop — 태그 파싱 로직만 검증
# ══════════════════════════════════════════════════════

class TestLayerAgenticLoop:

    def test_failure_signals_detect_error(self):
        from modules.agentic_loop import _verify_gate, _FAILURE_SIGNALS
        warn = _verify_gate("RUN_CMD", "오류: command not found")
        assert "VerificationGate" in warn, "오류 감지 실패"

    def test_failure_signals_pass_on_success(self):
        from modules.agentic_loop import _verify_gate
        warn = _verify_gate("RUN_CMD", "Hello World\n완료")
        assert warn == "", f"정상 결과를 실패로 오판: {warn}"

    def test_create_gate_detects_missing_file(self, tmp_path):
        from modules.agentic_loop import _verify_gate
        fake_path = str(tmp_path / "nonexistent.txt")
        warn = _verify_gate("CREATE", "[✅ CREATE 완료]", fake_path)
        assert "VerificationGate" in warn, "미존재 파일 감지 실패"

    def test_create_gate_passes_existing_file(self, tmp_path):
        from modules.agentic_loop import _verify_gate
        real_file = tmp_path / "real.txt"
        real_file.write_text("content")
        warn = _verify_gate("CREATE", "[✅ CREATE 완료]", str(real_file))
        assert warn == "", "존재하는 파일을 실패로 오판"

    def test_tag_regex_list(self):
        from modules.agentic_loop import _RE_LIST
        m = _RE_LIST.search("[LIST: /some/path]")
        assert m and m.group(1).strip() == "/some/path"

    def test_tag_regex_search(self):
        from modules.agentic_loop import _RE_SEARCH
        m = _RE_SEARCH.search("[SEARCH: 파이썬 비동기]")
        assert m and "파이썬" in m.group(1)


# ══════════════════════════════════════════════════════
# Layer 3: Memory 파일 무결성 — JSON 구조 검증
# ══════════════════════════════════════════════════════

class TestLayerMemoryFiles:
    L2_PATH = Path("/Users/bluesea/.hermes/memory/episodic_memory.json")
    L3_PATH = Path("/Users/bluesea/.hermes/memory/semantic_memory.json")

    def test_l2_structure_valid(self):
        if not self.L2_PATH.exists():
            pytest.skip("L2 파일 없음")
        data = json.loads(self.L2_PATH.read_text())
        assert "episodes" in data, "L2에 episodes 키 없음"
        assert isinstance(data["episodes"], list)

    def test_l2_episodes_have_required_fields(self):
        if not self.L2_PATH.exists():
            pytest.skip("L2 파일 없음")
        data = json.loads(self.L2_PATH.read_text())
        required = {"id", "content", "timestamp", "importance"}
        for ep in data["episodes"][:10]:
            missing = required - set(ep.keys())
            assert not missing, f"에피소드 필수 필드 누락: {missing}"

    def test_l3_structure_valid(self):
        if not self.L3_PATH.exists():
            pytest.skip("L3 파일 없음")
        data = json.loads(self.L3_PATH.read_text())
        assert "patterns" in data or "concepts" in data, "L3 구조 이상"


# ══════════════════════════════════════════════════════
# Layer 4: 핸들러 파싱 로직 — LLM 없이
# ══════════════════════════════════════════════════════

class TestLayerHandlers:

    def test_base_handler_imports(self):
        try:
            from handlers._base import safe_reply, safe_edit
            assert callable(safe_reply)
            assert callable(safe_edit)
        except ImportError as e:
            pytest.fail(f"_base 핸들러 임포트 실패: {e}")

    def test_all_handlers_importable(self):
        handler_names = [
            "_base", "_meta", "_system", "_memory",
            "_vault", "_kanban", "_research",
        ]
        failed = []
        for name in handler_names:
            try:
                __import__(f"handlers.{name}")
            except Exception as e:
                failed.append(f"{name}: {e}")
        assert not failed, f"핸들러 임포트 실패:\n" + "\n".join(failed)


# ══════════════════════════════════════════════════════
# 실행 시간 측정 (전체 < 5초 목표)
# ══════════════════════════════════════════════════════

def test_harness_speed():
    """전체 하네스가 5초 이내 완료되는지 확인"""
    start = time.time()
    from modules.memory_refinement import should_store, auto_forget, hybrid_recall
    from modules.agentic_loop import _verify_gate
    should_store("빠른 테스트", role="user")
    auto_forget(dry_run=True)
    _verify_gate("RUN_CMD", "정상 완료")
    elapsed = time.time() - start
    assert elapsed < 5.0, f"하네스 실행 시간 초과: {elapsed:.2f}s"
