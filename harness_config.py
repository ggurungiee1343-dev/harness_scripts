"""
harness_config.py — Mac Studio 환경 전용 하네스 설정
=====================================================
agent_harness.py 및 hermes_local.py 모두 이 파일을 공통 참조합니다.
경로나 임계값을 바꾸고 싶을 때 이 파일만 수정하면 됩니다.

위치: /Users/bluesea/Applications/Mjauto/Scripts/harness_config.py
"""

from pathlib import Path


class HarnessConfig:
    # ── 기본 경로 ─────────────────────────────────────────────────
    PROJECT_ROOT  = Path("/Users/bluesea/Applications/Mjauto")
    VAULT_PATH    = Path("/Users/bluesea/Applications/Mjobsidian")
    SCRIPTS_DIR   = PROJECT_ROOT / "Scripts"
    MODULES_DIR   = SCRIPTS_DIR / "modules"
    SKILLS_DIR    = Path("/Users/bluesea/.hermes/skills")

    # ── 하네스 상태 파일 ──────────────────────────────────────────
    PROGRESS_FILE    = PROJECT_ROOT / "PROGRESS.md"
    DIAGNOSTIC_LOG   = PROJECT_ROOT / "harness_diagnostic_log.json"
    STALENESS_DB     = PROJECT_ROOT / ".harness_staleness.json"
    HARNESS_MEMORY   = SCRIPTS_DIR / "harness_memory.json"

    # ── Cold-Start 필수 문서 목록 (Layer 1+2 체크) ────────────────
    # 존재하지 않으면 하네스가 실행을 거부함
    REQUIRED_DOCS: list[Path] = [
        PROJECT_ROOT / "AGENTS.md",               # 에이전트 진입점 가이드
        PROJECT_ROOT / "스크립트_정보.md",          # 시스템 통합 매뉴얼
        VAULT_PATH / "wiki/00_Meta/시스템 상태.md", # 공통 장부
    ]

    # ── 지식 부패 임계값 ──────────────────────────────────────────
    # 이 일수를 초과하고 파일 변경이 없으면 Staleness 경고 발령
    STALENESS_THRESHOLD_DAYS: int = 7

    # ── 기계 검증 DoD 명령어 (Layer 4) ───────────────────────────
    # 모두 통과해야 커밋 허용. 하나라도 실패하면 롤백 + 진단 루프 가동
    VERIFICATION_COMMANDS: list[str] = [
        # hermes_local.py 문법 검사
        "python -m py_compile /Users/bluesea/hermes/hermes_local.py",
        # 런타임가져오기(Import) 스모크 테스트 (중요 변수/가져오기 누락 방지)
        "PYTHONPATH=/Users/bluesea/hermes python3 -c 'import hermes_local'",
        # 핵심 모듈 문법 검사
        "python -m py_compile /Users/bluesea/Applications/Mjauto/Scripts/modules/executor.py",
        "python -m py_compile /Users/bluesea/Applications/Mjauto/Scripts/modules/verification_engine.py",
    ]

    # ── 선택적 검증 (존재할 때만 실행, 실패해도 롤백 안 함) ────────
    OPTIONAL_VERIFICATION: list[str] = [
        "pytest /Users/bluesea/Applications/Mjauto/Scripts/tests/ -q --tb=short",
        (
            "flake8 /Users/bluesea/Applications/Mjauto/Scripts/ "
            "--max-line-length=120 --exclude=__pycache__,tests"
        ),
    ]

    # ── 텔레그램 하네스 명령어 권한 ──────────────────────────────
    # /harness 명령어 사용 가능한 사용자 ID (hermes_local.py 의 ALLOWED_ID 와 동일)
    HARNESS_ALLOWED_USER_ID: int = 5365732604

    # ── Git 설정 ──────────────────────────────────────────────────
    # 격리 브랜치 prefix
    AGENT_BRANCH_PREFIX: str = "agent"
    # 진단 로그 최대 보관 수
    MAX_DIAGNOSTIC_LOGS: int = 50
