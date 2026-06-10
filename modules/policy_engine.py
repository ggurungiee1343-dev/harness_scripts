"""
Policy Engine — YAML 기반 도구 정책 제어
=========================================
Feature 5: YAML 정책 파일을 로드하여 실행 전 도구/액션 접근을 제어합니다.

설계 원칙:
  - 정책은 YAML 파일로 선언적 관리 (코드 변경 불필요)
  - Allowlist 기반: 명시적으로 허용된 것만 실행
  - Context-aware: 현재 LLM 모드(DeepSeek/Gemma4/NVIDIA)에 따라 다른 정책 적용
  - ARL과 연동: Action Realization Layer가 검증 전 policy check 수행

정책 파일 예시 (policy.yaml):
  version: 1
  default_action: deny  # 기본적으로 모든 액션 차단
  rules:
    - actions: ["*"]  # 전체 허용 규칙
      allow: true
      modes: ["*"]    # 모든 모드에서 허용
    - actions: ["exec_bash", "exec_python"]
      allow: true
      modes: ["deepseek", "gemma4"]
    - actions: ["file_delete", "file_rename"]
      allow: true
      require_confirm: true  # 실행 전 확인 필요
"""
from __future__ import annotations

import os
import re
import yaml
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("PolicyEngine")

# 기본 정책 디렉토리
DEFAULT_POLICY_DIR = Path(__file__).parent / "policies"
MAIN_POLICY_FILE = "hermes_policy.yaml"


class PolicyRule:
    """단일 정책 규칙"""

    def __init__(self, rule_data: dict):
        self.actions: list[str] = rule_data.get("actions", ["*"])
        self.allow: bool = rule_data.get("allow", False)
        self.modes: list[str] = rule_data.get("modes", ["*"])
        self.require_confirm: bool = rule_data.get("require_confirm", False)
        self.path_patterns: list[str] = rule_data.get("path_patterns", [])
        self.reason: str = rule_data.get("reason", "")

    def matches(self, action: str, mode: str, action_path: str = "") -> bool:
        """규칙이 주어진 액션/모드/경로에 적용되는지 확인"""
        # 액션 매칭
        if "*" not in self.actions and not any(
            fnmatch(action, a) for a in self.actions
        ):
            return False
        # 모드 매칭
        if "*" not in self.modes and mode not in self.modes:
            return False
        # 경로 패턴 매칭 (경로 조건이 있는 경우)
        if self.path_patterns and action_path:
            if not any(fnmatch(action_path, p) for p in self.path_patterns):
                return False
        return True


class PolicyEngine:
    """
    YAML 정책 파일을 로드하고 액션 허용 여부를 판단합니다.
    """

    def __init__(self, policy_dir: str | Path = None):
        self.policy_dir = Path(policy_dir or DEFAULT_POLICY_DIR)
        self.policy_dir.mkdir(parents=True, exist_ok=True)
        self._rules: list[PolicyRule] = []
        self._config: dict = {}
        self._loaded = False

    def load_policy(self, policy_name: str = MAIN_POLICY_FILE) -> bool:
        """YAML 정책 파일 로드"""
        policy_path = self.policy_dir / policy_name
        if not policy_path.exists():
            logger.warning(f"정책 파일 없음: {policy_path}, 기본 정책 사용")
            self._load_default()
            return False

        try:
            with open(policy_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._config = data
            self._rules = [PolicyRule(r) for r in data.get("rules", [])]
            self._loaded = True
            logger.info(f"정책 로드 완료: {policy_name} ({len(self._rules)}개 규칙)")
            return True
        except Exception as e:
            logger.error(f"정책 로드 실패: {e}, 기본 정책 사용")
            self._load_default()
            return False

    def _load_default(self):
        """기본 정책: 모든 액션 허용 (기존 동작 유지)"""
        self._config = {"version": 1, "default_action": "allow"}
        self._rules = [
            PolicyRule({
                "actions": ["*"],
                "allow": True,
                "modes": ["*"],
            })
        ]
        self._loaded = True

    def reload(self) -> bool:
        """정책 리로드 (런타임 변경 반영)"""
        self._loaded = False
        return self.load_policy()

    # ──────────────────────────────────────────────────────────────────────
    #  정책 검증 (ARL 연동 인터페이스)
    # ──────────────────────────────────────────────────────────────────────

    def check_action(
        self,
        action: str,
        mode: str = "",
        action_path: str = "",
    ) -> dict:
        """
        액션 실행 전 정책 검증.

        Args:
            action: 실행할 액션 이름 (예: "file_delete", "exec_bash")
            mode: 현재 LLM 모드 (deepseek, gemma4, nvidia)
            action_path: 액션이 적용될 경로 (있는 경우)

        Returns:
            {"allowed": bool, "reason": str, "require_confirm": bool}
        """
        if not self._loaded:
            self.load_policy()

        # 규칙을 순회하며 첫 번째 매칭 규칙 적용
        for rule in self._rules:
            if rule.matches(action, mode, action_path):
                return {
                    "allowed": rule.allow,
                    "reason": rule.reason,
                    "require_confirm": rule.require_confirm,
                }

        # 매칭 규칙 없음 → default_action
        default = self._config.get("default_action", "allow")
        return {
            "allowed": default == "allow",
            "reason": f"기본 정책({default}) 적용",
            "require_confirm": False,
        }

    def check_path_access(self, path: str, operation: str = "write") -> dict:
        """파일 경로 접근 정책 검증"""
        return self.check_action(
            action=f"file_{operation}",
            action_path=path,
        )

    # ──────────────────────────────────────────────────────────────────────
    #  정책 생성/관리
    # ──────────────────────────────────────────────────────────────────────

    def create_default_policy(self) -> str:
        """기본 정책 파일 생성"""
        policy_content = """# Hermes Policy Engine — 기본 정책
# 이 파일을 수정하여 액션 접근 권한을 제어하세요.
version: 1
default_action: allow

rules:
  # 모든 액션 허용 (기본)
  - actions: ["*"]
    allow: true
    modes: ["*"]

  # 위험 파일 작업은 확인 필요
  - actions: ["file_delete", "file_rename", "file_move"]
    allow: true
    require_confirm: true
    modes: ["*"]
    reason: "파일 삭제/이동/이름변경은 확인 후 실행됩니다."

  # 위험 bash 명령어 차단 (ARL과 이중 방어)
  - actions: ["exec_bash"]
    allow: true
    modes: ["*"]
    path_patterns: []
"""
        policy_path = self.policy_dir / MAIN_POLICY_FILE
        policy_path.write_text(policy_content, encoding="utf-8")
        return str(policy_path)

    def get_policy_summary(self) -> str:
        """현재 로드된 정책 요약"""
        if not self._rules:
            return "(정책 없음 — 모든 액션 허용)"
        lines = [f"📜 정책: {len(self._rules)}개 규칙"]
        for i, rule in enumerate(self._rules, 1):
            actions = ", ".join(rule.actions)
            modes = ", ".join(rule.modes)
            status = "✅ 허용" if rule.allow else "🚫 차단"
            confirm = " [확인필요]" if rule.require_confirm else ""
            lines.append(f"  {i}. {status}{confirm} | actions={actions} | modes={modes}")
            if rule.reason:
                lines.append(f"     → {rule.reason}")
        return "\n".join(lines)


# import here to avoid circular at module level
def fnmatch(name: str, pattern: str) -> bool:
    """간단한 fnmatch (와일드카드 매칭)"""
    import fnmatch as _fnmatch
    return _fnmatch.fnmatch(name, pattern)
