import os
import sys
import shutil
import asyncio
import openai
import unicodedata
import re
import fcntl
import time
from datetime import datetime, timezone, timedelta
import importlib
# executor 패키지는 Python 3.14에서 async 예약어 충돌로 사용 불가 → asyncio로 대체
import subprocess

# ── DeepSeek 프리픽스 캐싱 최적화: 고정 시스템 프롬프트 ──────────────
# system 메시지는 항상 동일해야 KV캐시 히트율이 올라감
# wiki context(변동부분)는 별도 user 메시지로 주입
_FIXED_SYS_PROMPT = """당신은 '하네스 에이전트(Hermes)'입니다. 박사님의 개인 통합 비서이자 모든 전문 모듈의 지휘관으로 동작합니다.

### [🛠️ AGENT OPERATIONAL GUIDE]
1. **통합 비서 정체성**: 당신은 '하네스 에이전트'이며, 모든 전문 모듈의 지휘관입니다.
2. **[🚨 절대 규칙] 파일 목록 조회**: 박사님이 파일 목록이나 폴더 내용을 물으실 때, [LIST: 경로] 태그를 반드시 사용해서 실제 파일 시스템을 조회하고, 그 결과만 보고해.
3. **번역 금지**: 영문 파일명(예: guardrails.md)을 임의로 한국어(예: 가드레일.md)로 번역하여 리스트를 만들지 마십시오. 대소문자와 확장자까지 100% 동일하게 사용하십시오.
4. **근거 기반 실행**: [LIST]나 [READ] 결과가 없는 정보는 존재하지 않는 정보로 간주하십시오.
5. **연쇄 행동**: 필요하다면 [LIST] -> [READ] -> [SAVE] 순서로 여러 태그를 연달아 사용하여 작업을 완수하십시오.
6. **웹 검색**: 인터넷 검색이나 최신 정보가 필요할 때는 반드시 [SEARCH: 검색어] 태그를 사용하여 실제 검색 결과를 얻은 후 답변하십시오.
7. **[🚨 카파시 4대 원칙]** 1) 지시가 모호하면 코딩 전 질문하고 대안을 제시하십시오, 2) 가장 단순한 구조의 코드를 구현하십시오, 3) 대상 외에 무관한 파일/코드는 절대 수정하지 마십시오 (Scope 엄수), 4) 불확실하면 솔직히 명시하십시오.
8. **미사여구 배제 (No Filler)**: 대답 시작 시 '물론입니다', '좋은 질문입니다' 등 무의미한 서두 표현을 배제하고 즉시 본론 및 결과로 답변하십시오.
9. **파일 작업 태그**: 다음과 같은 파일 작업 태그를 사용할 수 있습니다.
   - [DELETE: /절대/경로/파일] : 파일을 휴지통으로 이동 (영구 삭제 아님, 확인 절차 거침)
   - [MOVE: /원본/경로 -> /목적지/경로] : 파일/폴더 이동
   - [COPY: /원본/경로 -> /목적지/경로] : 파일/폴더 복사
   - [RENAME: /원본/경로 -> /새이름/경로] : 파일/폴더 이름 변경
   - [CREATE: /경로/파일](내용)[/CREATE] : 새 파일 생성
   - [RUN_CMD: 쉘 명령어] : 터미널에서 Bash 명령어 실행 (예: 시스템 상태 확인, 스크립트 구동 등)
   박사님이 '이 파일 지워줘', '저 폴더로 옮겨줘', '터미널에서 확인해줘' 등 자연어로 파일 조작이나 시스템 명령을 요청하면 위 태그를 사용하여 자율적으로 실행하세요. DELETE 작업은 확인 후 실행됩니다.
10. **대기 요청 금지**: 사용자의 지시를 수행하기 위해 [CREATE], [RUN_CMD], [SEARCH] 등의 태그가 필요하다면 "잠시 대기"라고 말하지 마십시오. 즉시 태그를 생성하고 결과를 요청하여 연속적으로 작업을 마무리하십시오.
11. **[📢 응답 품질 원칙]**
    - **핵심 먼저**: 첫 문장은 반드시 "무슨 일이 있었나 / 무엇을 발견했나"로 시작하십시오. 배경 설명, 옵션 나열, 계획 서술은 핵심 이후에 배치하십시오.
    - **증거 기반 보고**: 진행 상황이나 완료 여부는 실제 툴 실행 결과(태그 응답)로만 보고하십시오. 확인되지 않은 내용은 "아직 미확인"으로 명시하십시오.
    - **질문·사고 중엔 분석만**: 박사님이 질문하거나 생각을 말씀하실 때(실행 요청이 없을 때)는 분석·진단 결과만 보고하십시오. 수정·실행은 명시적 요청이 있을 때만 하십시오.
    - **이미 결정된 것 재논의 금지**: 대화에서 이미 확정된 사실이나 결정을 다시 도출하거나 재검토하지 마십시오.

[💡 Few-Shot Example: 파일 목록 요청 처리 방법]
User: 00_Meta 폴더에 어떤 파일이 있어?
Assistant: [LIST: /Users/bluesea/Applications/Mjobsidian/wiki/00_Meta]
(주의: 위와 같이 오직 태그 하나만 출력하고 대기해야 합니다. 절대로 혼자서 파일 이름을 나열하지 마십시오.)"""

_CAVEMAN_SYS_PROMPT = """당신은 박사님의 통합 비서 Hermes. 핵심 규칙만 따를 것:
1) 모르면 [LIST:경로], [READ:파일] 태그로 실제 조회
2) [SEARCH:질의어] 로 웹 검색, [RUN_CMD:명령] 으로 bash 실행
3) 미사여구 배제, 즉시 본론
4) 파일 작업: [DELETE/MOVE/COPY/RENAME/CREATE/RUN_CMD] 태그 사용 (DELETE는 확인 후 실행)
5) 불확실하면 솔직히 명시, 무관한 파일 수정 금지
6) 번역 금지: 영문 파일명을 절대 번역하지 말 것
7) 대기 금지: 행동이 필요하면 즉시 태그를 출력하라. "잠시 대기" 등 말하지 말 것.

가능한 가장 적은 토큰으로 핵심만 응답하라."""


CAVEMAN_FILE = "/Users/bluesea/.hermes/caveman.txt"

def get_sys_prompt():
    if os.path.exists(CAVEMAN_FILE):
        with open(CAVEMAN_FILE, "r") as f:
            if f.read().strip() == "on":
                return _CAVEMAN_SYS_PROMPT
    return _FIXED_SYS_PROMPT




# wiki context 5분 캐시 (파일 I/O 최소화)
_wiki_ctx_cache = {"text": "", "ts": 0.0}
_WIKI_CACHE_TTL = 300  # 5분

# Telegram imports
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

_module_cache = {}
def _load_module(name: str):
    if name not in _module_cache:
        _module_cache[name] = importlib.import_module(name)
    return _module_cache[name]

# 1. 설정 및 경로 로드
import config
sys.path.insert(0, str(config.SCRIPTS_DIR))   # modules/ 하위 폴더 접근
sys.path.insert(0, str(config.MACBOT_DIR))    # MacBot/ 명시적 경로 추가 (P1 Fix)

# 2. 싱글톤 보장 (중복 실행 방지)
def ensure_singleton():
    lock_file = "/tmp/harness_agent.lock"
    my_pid = os.getpid()
    os.system(f"ps -ef | grep 'harness_agent.py' | grep -v grep | grep -v {my_pid} | awk '{{print $2}}' | xargs kill -9 > /dev/null 2>&1")
    
    global fp
    fp = open(lock_file, 'w')
    try:
        fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.write(str(my_pid))
        fp.flush()
    except (IOError, OSError):
        print("⚠️ 이미 하네스 에이전트가 실행 중입니다.")
        sys.exit(1)

# ensure_singleton()은 __main__ 진입 시에만 호출 (라이브러리 모드에서는 호출 안 함)

# MacBot logic_engine 별도 로드 (try 블록 외부로 분리)
try:
    from MacBot import logic_engine as _le_mod
    logic_engine = _le_mod
except ImportError as e:
    logic_engine = None
    print(f"⚠️ MacBot logic_engine 로드 실패: {e}")

# 3. 전문 모듈 로드 (True Lazy Loading)
class LazyService:
    def __init__(self, module_name, class_name, *args, **kwargs):
        self._module_name = module_name
        self._class_name = class_name
        self._args = args
        self._kwargs = kwargs
        self._instance = None

    def __getattr__(self, name):
        if self._instance is None:
            import importlib
            mod = importlib.import_module(f"modules.{self._module_name}")
            cls = getattr(mod, self._class_name)
            self._instance = cls(*self._args, **self._kwargs)
            print(f"🚀 [LazyLoad] {self._class_name} 인스턴스화 됨")
        return getattr(self._instance, name)

# 4. 서비스 인스턴스 초기화 (Proxy 객체들)
wiki = LazyService("wiki_manager", "WikiManager", str(config.WORK_SPACE))
history = LazyService("history_manager", "HistoryManager", str(config.SCRIPTS_DIR / "harness_memory.json"))
file_m = LazyService("file_manager", "FileManager", str(config.WORK_SPACE), bot_author="헤르메스봇1")
memory = LazyService("bio_memory_engine", "BioMemoryEngine", str(config.WORK_SPACE))
sys_mon = LazyService("system_monitor", "SystemMonitor", threshold_gb=config.MEM_THRESHOLD_GB)
news = LazyService("news_engine", "NewsEngine", str(config.WORK_SPACE))
git = LazyService("git_manager", "GitManager", str(config.GIT_REPO_PATH), config.GIT_REMOTE_NAME)
cog_engine = LazyService("cognitive_engine", "CognitiveEngine", str(config.WORK_SPACE))
audit = LazyService("audit_engine", "AuditEngine", str(config.WORK_SPACE))
scanner = LazyService("model_scanner", "ModelScanner")
ctx_builder = LazyService("hermes_context_builder", "ContextBuilder", str(config.WORK_SPACE))
ingest_inst = LazyService("ingest_engine", "IngestEngine", source_dir=str(config.CLIPPINGS_DIR), dest_dir=str(config.WIKI_DEST_DIR), llm_func=None)

# Logic Engine 초기화 (지연 로드)
logic = None
if logic_engine:
    class LogicProxy:
        def __init__(self):
            self._logic = None
        def __getattr__(self, name):
            if self._logic is None:
                self._logic = logic_engine.LogicEngine(str(config.WORK_SPACE), {
                    "file_manager": file_m,
                    "memory_engine": memory,
                    "system_monitor": sys_mon,
                    "news_engine": news,
                    "ingest_engine": ingest_inst
                })
                print(f"🚀 [LazyLoad] LogicEngine 인스턴스화 됨")
            return getattr(self._logic, name)
    logic = LogicProxy()

# 세만틱 엔진 지연 로드 (P1 Fix: Fallback 추가)
semantic_instance = None
def get_semantic():
    global semantic_instance
    if semantic_instance is None:
        try:
            from MacBot import semantic_engine as sem_mod
            semantic_instance = sem_mod.SemanticEngine(str(config.WORK_SPACE))
        except ImportError:
            from modules import semantic_engine as sem_mod  # Fallback
            semantic_instance = sem_mod.SemanticEngine(str(config.WORK_SPACE))
    return semantic_instance

# ── LLM 엔진 레이어 (modules/llm_engines.py로 분리) ──────────
from modules.llm_engines import (
    nvidia_client, local_client, deepseek_client,
    MODE_QWEN14B, MODE_DEEPSEEK, MODE_NVIDIA, ALL_MODES,
    FALLBACK_CHAIN, MODE_LABELS, MODE_FILE, FALLBACK_FILE,
    get_current_mode, set_mode, is_auto_fallback, set_auto_fallback, clear_auto_fallback,
    _call_nvidia, _call_local, _call_deepseek,
    call_primary_with_fallback as _call_primary_with_fallback,
    _fallback_notify_bot, _fallback_notify_chat_id,
)
import modules.llm_engines as _llm_eng

# [v3.7] IngestEngine에 LLM 래퍼 연결 (지능형 파일 분류 활성화)
async def ingest_llm_wrapper(prompt):
    messages = [{"role": "user", "content": prompt}]
    ans, _ = await get_llm_response(messages)
    return ans

if ingest_inst:
    ingest_inst.llm_func = ingest_llm_wrapper

# --- [Hybrid Router 통합 (Action Realization Layer)] ---
from hybrid_router import HybridRouter

hybrid_router = HybridRouter(
    local_llm_func=_call_primary_with_fallback,
    api_llm_func=_call_deepseek,
    provider_funcs={
        "deepseek": _call_deepseek,
        "qwen14b": _call_local,
        "nim": _call_nvidia,
    }
)

import hybrid_router as _hr_mod
_hr_mod.patch_router(hybrid_router)

async def get_llm_response(messages):
    mode = get_current_mode()
    return await hybrid_router.route_and_execute(messages, force_primary=mode)



# ── 파일 작업 레이어 (modules/file_ops_agent.py로 분리) ────────
from modules.file_ops_agent import (
    _file_op_pending, is_path_allowed as _is_path_allowed,
    safe_remove as _safe_remove, handle_file_op as _handle_file_op,
)
from modules.context_assembler import assemble_context
from modules.agentic_loop import run_agentic_loop
from modules.command_router import route_command
from modules.response_handler import handle_llm_response


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_user.id != config.ALLOWED_ID:
        return
    user_text = update.message.text

    if _llm_eng._fallback_notify_bot is None:
        _llm_eng._fallback_notify_bot = context.bot
        _llm_eng._fallback_notify_chat_id = update.effective_chat.id

    if await route_command(
        update, context, user_text,
        file_op_pending=_file_op_pending,
        safe_remove=_safe_remove,
        get_current_mode=get_current_mode,
        ALL_MODES=ALL_MODES,
        MODE_LABELS=MODE_LABELS,
        sys_mon=sys_mon,
        memory=memory,
        history=history,
        ingest_inst=ingest_inst,
        get_semantic=get_semantic,
        logic=logic,
        get_llm_response=get_llm_response,
    ):
        return

    await handle_llm_response(
        update, context, user_text,
        history=history,
        wiki=wiki,
        memory=memory,
        config=config,
        get_llm_response=get_llm_response,
        file_m=file_m,
        ctx_builder=ctx_builder,
        get_sys_prompt=get_sys_prompt,
    )

# ──────────────────────────────────────────────────────────────────
# harness_agent.py는 라이브러리 모드로 동작합니다.
# ──────────────────────────────────────────────────────────────────
# [Feature 5] Policy Engine 초기화 함수
# ──────────────────────────────────────────────────────────────────
def _init_policy_engine():
    """Policy Engine 초기화 (모듈 로드 시 자동 실행)"""
    try:
        from modules.policy_engine import PolicyEngine
        from modules.action_realization_layer import ActionRealizationLayer
        pe = PolicyEngine()
        pe.load_policy()
        ActionRealizationLayer.get_instance(policy_engine=pe)
        print(f"🛡️ Policy Engine 활성화")
        print(pe.get_policy_summary())
    except Exception as e:
        print(f"⚠️ Policy Engine 초기화 실패: {e}")


# ──────────────────────────────────────────────────────────────────
# <단독 실행 진입점>
#
# 단독 실행(직접 python3 harness_agent.py)은 테스트/디버그 용도로만 사용하세요.
# ──────────────────────────────────────────────────────────────────
# ============================================================
# [Feature 5] Policy Engine 초기화 (모듈 로드 시 자동 실행)
# ============================================================
_init_policy_engine()
# ============================================================

if __name__ == "__main__":
    ensure_singleton()

    async def auto_heal_loop():
        while True:
            try: sys_mon.auto_heal(os.path.join(config.SCRIPTS_DIR, "recovery_system.sh"))
            except: pass  # noqa — SYCL500: harness_agent는 독립 실행 시 무시
            await asyncio.sleep(60)

    async def main():
        app = Application.builder().token(config.TELEGRAM_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        asyncio.create_task(auto_heal_loop())
        async with app:
            await app.initialize(); await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            while True: await asyncio.sleep(3600)

    try: asyncio.run(main())
    except KeyboardInterrupt: pass
