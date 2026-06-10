import os
import time
from pathlib import Path
from dotenv import load_dotenv

# 타임존 KST 강제 설정 (모든 봇/스크립트 공통)
os.environ['TZ'] = 'Asia/Seoul'
if hasattr(time, 'tzset'):
    time.tzset()

# 📍 경로 설정
BASE_DIR = Path("/Users/bluesea/Applications/Mjauto").resolve()
WORK_SPACE = Path("/Users/bluesea/Applications/Mjobsidian").resolve()
SCRIPTS_DIR = BASE_DIR / "Scripts"
MODULES_DIR = SCRIPTS_DIR / "modules"
MACBOT_DIR = SCRIPTS_DIR / "MacBot"

# 환경 변수 로드
load_dotenv(BASE_DIR / ".env")

# 🤖 텔레그램 및 API 설정
TELEGRAM_TOKEN = os.getenv("HARNESS_BOT_TOKEN")
MACBOT_TOKEN = os.getenv("CODEX_BOT_TOKEN")
ALLOWED_ID = int(os.getenv("ALLOWED_USER_ID", "5365732604"))
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
CAPT_NVIDIA_API_KEY = os.getenv("CAPT_NVIDIA_API_KEY", "")
ANTIGRAVITY_NVIDIA_API_KEY = os.getenv("ANTIGRAVITY_NVIDIA_API_KEY", "")
CODEX_API_KEY = os.getenv("CODEX_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-026123bd12444959a6713013967f6672")
LM_STUDIO_BASE_URL = "http://127.0.0.1:8080/v1"
LM_STUDIO_API_KEY = "lm-studio"

# 🧠 메모리 감시 설정
MEM_THRESHOLD_GB = 0.5

# ☁️ Git 설정 (Auto-Sync용)
GIT_REPO_PATH = WORK_SPACE
GIT_REMOTE_NAME = "origin"

# 📂 Gardening 설정 (Ingest용)
CLIPPINGS_DIR = WORK_SPACE / "Clippings"
RAW_DIR = WORK_SPACE / "raw"
WIKI_DEST_DIR = WORK_SPACE / "wiki"
