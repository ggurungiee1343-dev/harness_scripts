"""
test_imports.py — 모든 모듈의 import 체인 검증
===============================================
각 모듈/핸들러가 import 시점에 오류 없이 로드되는지 확인.
modules/는 개별 import, handlers/는 __init__.py 전체 로드.

Cf. conftest.py에서 telegram, numpy, requests, bs4를 mock 처리.
"""

import importlib
import pkgutil
import pytest
import sys
from pathlib import Path

SCRIPTS_DIR = "/Users/bluesea/Applications/Mjauto/Scripts"
MODULES_DIR = f"{SCRIPTS_DIR}/modules"
HANDLERS_DIR = f"{SCRIPTS_DIR}/handlers"

# ── 제외 대상 ──────────────────────────────────────────────
# 1. 테스트 파일
# 2. __pycache__
# 3. __init__.py (handlers/는 __init__.py로 전체 로드)
# 4. 특수 케이스: dream_scheduler는 runtime 의존성 많음 → standalone import
EXCLUDE_MODULES = {
    "__pycache__", "__init__",
}

# 싱글턴 생성 등 import 부작용이 있는 모듈 — 경고 로그 허용
IMPORT_SIDE_EFFECTS_OK = {
    "dream_scheduler",  # shared instances
    "bio_memory_engine",  # sys.path.append 부작용
}


def _discover_modules(directory: str) -> list:
    """디렉토리 내 .py 파일 목록 (모듈명)"""
    py_files = sorted(Path(directory).glob("*.py"))
    modules = []
    for f in py_files:
        name = f.stem
        if name.startswith("_") or name in EXCLUDE_MODULES:
            continue
        modules.append(name)
    return modules


# ===== modules/ =====

def _import_module(module_name: str, package_dir: str):
    """모듈을 패키지 경로에서 import하여 반환"""
    spec = importlib.util.spec_from_file_location(
        module_name, f"{package_dir}/{module_name}.py"
    )
    assert spec is not None, f"spec not found for {module_name}"
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("module_name", _discover_modules(MODULES_DIR))
def test_module_import(module_name):
    """modules/*.py 개별 import"""
    mod = _import_module(module_name, MODULES_DIR)
    assert mod is not None
    assert hasattr(mod, "__file__")


def test_all_modules_import():
    """modules/ 전체를 순회하며 단일 import 실패가 없어야 함"""
    modules = _discover_modules(MODULES_DIR)
    failures = []
    for name in modules:
        try:
            _import_module(name, MODULES_DIR)
        except Exception as e:
            failures.append(f"  ❌ {name}: {e}")
    assert not failures, f"Import 실패 ({len(failures)}건):\n" + "\n".join(failures)


# ===== handlers/ =====

def test_handlers_init_import():
    """handlers/__init__.py 전체 로드 — telegram mock 필요"""
    spec = importlib.util.spec_from_file_location(
        "handlers", f"{HANDLERS_DIR}/__init__.py"
    )
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["handlers"] = mod
    spec.loader.exec_module(mod)
    assert mod is not None


@pytest.mark.parametrize("handler_name", [
    "_base", "_system", "_memory", "_file",
    "_kanban", "_orchestrator", "_research", "_ui",
])
def test_handler_module_import(handler_name):
    """handlers/_.py 개별 import (telegram mock)"""
    spec = importlib.util.spec_from_file_location(
        f"handlers.{handler_name}", f"{HANDLERS_DIR}/{handler_name}.py"
    )
    assert spec is not None, f"spec not found for handlers/{handler_name}.py"
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[f"handlers.{handler_name}"] = mod
    spec.loader.exec_module(mod)
    assert mod is not None


# ===== 루트 모듈 =====

@pytest.mark.parametrize("root_module", [
    "hybrid_router",
    "hermes_local",
])
def test_root_module_import(root_module):
    """Scripts/ 루트 수준 .py 개별 import"""
    spec = importlib.util.spec_from_file_location(
        root_module, f"{SCRIPTS_DIR}/{root_module}.py"
    )
    assert spec is not None, f"spec not found for {root_module}.py"
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[root_module] = mod
    spec.loader.exec_module(mod)
    assert mod is not None
