"""
conftest.py — 헤르메스 단위 테스트 공통 설정
===============================================
- sys.path 구성 (Scripts/, modules/)
- numpy mock (bio_memory_engine가 import 필요)
- telegram mock (handlers/ import 방지)
- 임시 DB / 임시 디렉토리 fixture
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# ── 1. 경로 등록 ───────────────────────────────────────────
_SCRIPTS_DIR = "/Users/bluesea/Applications/Mjauto/Scripts"
_MODULES_DIR = os.path.join(_SCRIPTS_DIR, "modules")
for _p in (_SCRIPTS_DIR, _MODULES_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 2. telegram mock (handlers/ import 체인용) ─────────────
_telegram_mock = MagicMock()
_Update_mock = MagicMock()
_telegram_mock.Update = _Update_mock
_telegram_mock.ReplyKeyboardMarkup = MagicMock()
_telegram_mock.KeyboardButton = MagicMock()
_telegram_mock.InlineKeyboardButton = MagicMock()
_telegram_mock.InlineKeyboardMarkup = MagicMock()
sys.modules["telegram"] = _telegram_mock

_ContextTypes_mock = MagicMock()
_ContextTypes_mock.DEFAULT_TYPE = MagicMock()
sys.modules["telegram.ext"] = MagicMock()
sys.modules["telegram.ext"].ContextTypes = _ContextTypes_mock
sys.modules["telegram.ext"].Application = MagicMock()
sys.modules["telegram.ext"].CommandHandler = MagicMock()
sys.modules["telegram.ext"].MessageHandler = MagicMock()
sys.modules["telegram.ext"].filters = MagicMock()
sys.modules["telegram.ext"].CallbackQueryHandler = MagicMock()
sys.modules["telegram.constants"] = MagicMock()
sys.modules["telegram.constants"].ChatAction = MagicMock()
sys.modules["telegram.error"] = MagicMock()
sys.modules["telegram.error"].Conflict = type("Conflict", (Exception,), {})
sys.modules["telegram.error"].TelegramError = type("TelegramError", (Exception,), {})

# ── 4. 외부 API mock (requests-rich 모듈용) ────────────────
sys.modules["requests"] = MagicMock()
sys.modules["bs4"] = MagicMock()
sys.modules["bs4"].BeautifulSoup = MagicMock()

# ── 4. Shared fixtures ────────────────────────────────────


@pytest.fixture
def scripts_dir():
    """Scripts/ 디렉토리 절대 경로"""
    return _SCRIPTS_DIR


@pytest.fixture
def temp_db(tmp_path):
    """임시 SQLite DB 파일 경로 — KanbanDB 테스트용"""
    return str(tmp_path / "test_kanban.db")


@pytest.fixture
def temp_skill_dir(tmp_path):
    """임시 스킬 디렉토리 — _validate_skill 테스트용"""
    d = tmp_path / "skills" / "test_skill"
    d.mkdir(parents=True)
    return d
