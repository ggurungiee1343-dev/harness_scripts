"""
test_skill_evolver.py — _validate_skill smoke test
===================================================
- import 검증
- _validate_skill()가 유효/무효 스킬 디렉토리를 올바르게 판별하는지 확인
- 필수 메타데이터: **생성일** **트리거** **카테고리**
"""

import importlib.util
import pytest
from pathlib import Path

MODULE_PATH = "/Users/bluesea/Applications/Mjauto/Scripts/modules/skill_evolver.py"


def _import_skill_evolver():
    spec = importlib.util.spec_from_file_location("skill_evolver", MODULE_PATH)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VALID_SKILL_MD = """# SKILL: test-skill

**생성일**: 2026-05-26
**트리거**: pytest 실행 시
**카테고리**: test

This skill has enough content to pass the 50-character threshold for validation.
"""


# ── Import ──────────────────────────────────────────────────

def test_import():
    """skill_evolver import 성공"""
    mod = _import_skill_evolver()
    assert hasattr(mod, "_validate_skill")
    assert hasattr(mod, "SkillEvolver")
    assert hasattr(mod, "find_similar_skill")


# ── _validate_skill ─────────────────────────────────────────

class TestValidateSkill:
    """_validate_skill() 검증 로직"""

    def test_valid_skill(self, temp_skill_dir):
        """유효한 SKILL.md → True"""
        mod = _import_skill_evolver()
        skill_md = temp_skill_dir / "SKILL.md"
        skill_md.write_text(VALID_SKILL_MD, encoding="utf-8")
        assert mod._validate_skill(temp_skill_dir) is True

    def test_missing_skill_md(self, temp_skill_dir):
        """SKILL.md 없음 → False"""
        mod = _import_skill_evolver()
        assert mod._validate_skill(temp_skill_dir) is False

    def test_empty_skill_md(self, temp_skill_dir):
        """빈 SKILL.md → False"""
        mod = _import_skill_evolver()
        skill_md = temp_skill_dir / "SKILL.md"
        skill_md.write_text("   ", encoding="utf-8")
        assert mod._validate_skill(temp_skill_dir) is False

    def test_skill_too_short(self, temp_skill_dir):
        """50자 미만 SKILL.md → False"""
        mod = _import_skill_evolver()
        skill_md = temp_skill_dir / "SKILL.md"
        # 50자 미만이면서도 # SKILL: 헤더가 있는 내용
        skill_md.write_text("# SKILL: x", encoding="utf-8")
        assert mod._validate_skill(temp_skill_dir) is False

    def test_skill_with_minimal_content(self, temp_skill_dir):
        """50자 이상 + 모든 필수 필드 포함 → True"""
        mod = _import_skill_evolver()
        skill_md = temp_skill_dir / "SKILL.md"
        skill_md.write_text(VALID_SKILL_MD, encoding="utf-8")
        assert mod._validate_skill(temp_skill_dir) is True

    def test_skill_missing_required_field(self, temp_skill_dir):
        """**카테고리** 누락 → False"""
        mod = _import_skill_evolver()
        skill_md = temp_skill_dir / "SKILL.md"
        skill_md.write_text(
            "# SKILL: bad-skill\n\n**생성일**: 2026-05-26\n**트리거**: test\n\nMissing category field.\n",
            encoding="utf-8",
        )
        assert mod._validate_skill(temp_skill_dir) is False

    def test_skill_no_skill_header(self, temp_skill_dir):
        """'# SKILL:' 헤더 없음 → False"""
        mod = _import_skill_evolver()
        skill_md = temp_skill_dir / "SKILL.md"
        skill_md.write_text(
            "# Not a skill\n\n**생성일**: 2026-05-26\n**트리거**: test\n**카테고리**: test\n\nContent here.\n",
            encoding="utf-8",
        )
        assert mod._validate_skill(temp_skill_dir) is False

    def test_skill_corrupted_encoding(self, temp_skill_dir):
        """UTF-8 디코딩 불가 → False"""
        mod = _import_skill_evolver()
        skill_md = temp_skill_dir / "SKILL.md"
        skill_md.write_bytes(b"\xff\xfe\x00\x01invalid bytes")
        assert mod._validate_skill(temp_skill_dir) is False

    def test_skill_subdir_ignored(self, temp_skill_dir):
        """하위 디렉토리 검증X, SKILL.md만 확인"""
        mod = _import_skill_evolver()
        sub = temp_skill_dir / "sub_dir"
        sub.mkdir()
        (sub / "SKILL.md").write_text(VALID_SKILL_MD, encoding="utf-8")
        # 최상위 SKILL.md 없음 → False
        assert mod._validate_skill(temp_skill_dir) is False
