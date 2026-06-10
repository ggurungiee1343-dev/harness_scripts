"""
hermes_handlers.py — 메인 명령어, 인텔리전스 및 지식 연동 핸들러
==============================================================
웰컴, 도움말, 질문(/ask, /cove, /web), 지식이관(/ingest),
드러밍(/dreaming), 배시(/exec), 핫클립(/clip) 등을 처리합니다.
"""
import os
import sys
import uuid
import logging
import datetime
import shutil
import signal
import subprocess
import asyncio
import time
from pathlib import Path
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from modules.kanban_manager import KanbanDB
from modules.audit_engine import AuditEngine
from telegram.ext import ContextTypes

# === Style Profile 캐시 ===
_style_cache: dict = {"text": "", "ts": 0.0}
_STYLE_CACHE_TTL = 3600  # 1시간

async def _get_style_profile() -> str:
    """style_profile.md 읽기 — 1시간 TTL 캐시, 1000자 truncation"""
    now = time.time()
    if now - _style_cache["ts"] < _STYLE_CACHE_TTL and _style_cache["text"]:
        return _style_cache["text"]
    profile_paths = [
        "/Users/bluesea/Applications/Mjobsidian/wiki/Obsidian Codex/style_profile.md",
        "/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta/style_profile.md",
    ]
    for p in profile_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                simplified = content[:1200]
                _style_cache["text"] = simplified
                _style_cache["ts"] = now
                return simplified[:1000]
            except Exception:
                continue
    return ""

# === 감사 엔진 인스턴스 ===
_audit_engine = AuditEngine()
# === 경로 등록 ===
sys.path.append('/Users/bluesea/.hermes/plugins')
sys.path.append('/Users/bluesea/.hermes/skills/knowledge/wiki/scripts')
sys.path.append('/Users/bluesea/.hermes/skills/brain/cognitive/scripts')
sys.path.append('/Users/bluesea/.hermes/skills/devops/executor/scripts')
sys.path.append('/Users/bluesea/Applications/Mjauto/Scripts/modules')
# === 모듈 임포트 ===
from hybrid_router import router
from wiki_manager import WikiManager
from verification_engine import verifier
from cove_engine import cove_engine_instance  # ~/Applications/Mjauto/Scripts/cove_engine.py
from executor import execute_bash_command
from ingest_engine import IngestEngine
from memory_engine import MemoryEngine
from system_monitor import SystemMonitor
from web_reader import analyze_url
from modules.action_realization_layer import ActionRealizationLayer
action_layer = ActionRealizationLayer.get_instance()
from modules.dialectic_layer import update_user_persona
from hermes_local import check_user, secure_path, history_mgr, wiki_mgr, BASE_DIR, PENDING_TASKS, _make_keyboard

# === 로거 ===
logger = logging.getLogger('HermesOrchestrator')

async def add_to_history(role: str, content: str) -> None:
    """대화 히스토리 + Bio-Memory 동시 저장"""
    history_mgr.add_message(role, content)
    try:
        from hermes_memory_patch import bio_add_message
        bio_add_message(role, content)
    except Exception as e:
        logger.error(f'❌ Bio-Memory 기록 실패: {e}')


# 콜백 처리는 _callbacks.py로 분리 (2026-06-09)
from handlers._callbacks import handle_button_callback

async def _get_mem_info() -> dict:
    """vm_stat 파싱 → 메모리 통계 dict 반환"""
    total_proc = await asyncio.create_subprocess_shell(
        'sysctl hw.memsize', stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    total_out, _ = await total_proc.communicate()
    total_bytes = int(total_out.decode().strip().split(':')[1].strip())
    total_gb = total_bytes / (1024 ** 3)

    vm_proc = await asyncio.create_subprocess_shell(
        'vm_stat', stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    vm_out, _ = await vm_proc.communicate()
    vm_lines = vm_out.decode().strip().split('\n')

    page_size = 16384
    stats = {'free': 0, 'active': 0, 'wired': 0, 'cached': 0}
    for line in vm_lines:
        if 'Pages free' in line:
            stats['free'] = int(line.split(':')[1].strip().rstrip('.'))
        elif 'Pages active' in line:
            stats['active'] = int(line.split(':')[1].strip().rstrip('.'))
        elif 'Pages wired down' in line:
            stats['wired'] = int(line.split(':')[1].strip().rstrip('.'))
        elif 'File-backed pages' in line:
            stats['cached'] = int(line.split(':')[1].strip().rstrip('.'))

    to_gb = lambda pages: (pages * page_size) / (1024 ** 3)
    used_gb = to_gb(stats['active'] + stats['wired'])
    return {
        'total_gb': total_gb,
        'used_gb': used_gb,
        'used_pct': (used_gb / total_gb * 100) if total_gb > 0 else 0,
        'cached_gb': to_gb(stats['cached']),
        'free_gb': to_gb(stats['free']),
    }


# 텔레그램 메시지 길이 제한 (4096 UTF-8 문자)
_TG_MSG_LIMIT = 3800

def _split_text(text: str, chunk_size: int = None) -> list:
    """개행 기준으로 텍스트를 chunk_size 이하로 분할"""
    if chunk_size is None:
        chunk_size = _TG_MSG_LIMIT
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    current = ""
    for line in text.split('\n'):
        test = current + line + '\n' if current else line + '\n'
        if len(test) > chunk_size and current:
            chunks.append(current.rstrip())
            current = line + '\n'
        else:
            current = test
    if current.rstrip():
        chunks.append(current.rstrip())
    if len(chunks) == 1 and len(text) > chunk_size:
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    return chunks


async def _reply_long(target, text: str, parse_mode: str = 'Markdown', chunk_size: int = None, **kwargs) -> None:
    """긴 텍스트를 여러 Telegram 메시지로 분할 전송
    파싱 실패 시 자동 폴백 (entities 파싱 에러 방지)"""
    if chunk_size is None:
        chunk_size = _TG_MSG_LIMIT
    chunks = _split_text(text, chunk_size)
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        if total == 1 or i == 0:
            try:
                await target.reply_text(chunk, parse_mode=parse_mode, **kwargs)
            except Exception:
                await target.reply_text(chunk, **kwargs)
        else:
            try:
                await target.reply_text(
                    f"⬇️ **({i+1}/{total})**\n\n{chunk}",
                    parse_mode=parse_mode
                )
            except Exception:
                await target.reply_text(f"⬇️ ({i+1}/{total})\n\n{chunk}")


async def _edit_or_send_long(msg, text: str, parse_mode: str = 'Markdown', chunk_size: int = None) -> None:
    """긴 텍스트 처리: 짧으면 edit_text, 길면 새 메시지로 분할 전송
    파싱 실패 시 자동 폴백 (entities 파싱 에러 방지)"""
    if chunk_size is None:
        chunk_size = _TG_MSG_LIMIT
    if len(text) <= chunk_size:
        try:
            await msg.edit_text(text, parse_mode=parse_mode)
            return
        except Exception:
            try:
                await msg.edit_text(text)
                return
            except Exception:
                pass
    chat_id = msg.chat_id if hasattr(msg, 'chat_id') else None
    if chat_id:
        chunks = _split_text(text, chunk_size)
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            if total == 1 or i == 0:
                try:
                    await msg.reply_text(chunk, parse_mode=parse_mode)
                except Exception:
                    await msg.reply_text(chunk)
            else:
                try:
                    await msg.reply_text(
                        f"⬇️ **({i+1}/{total})**\n\n{chunk}",
                        parse_mode=parse_mode
                    )
                except Exception:
                    await msg.reply_text(f"⬇️ ({i+1}/{total})\n\n{chunk}")


async def _call_llm(prompt: str, provider: str = None) -> str:
    """LLM 호출 유틸리티 — provider=None이면 현재 모드(Gemma4/DeepSeek/NVIDIA) 따라감"""
    if provider is None:
        from harness_agent import get_llm_response
        try:
            style_text = await _get_style_profile()
        except Exception:
            style_text = ""
        if style_text:
            messages = [
                {"role": "system", "content": f"[MJ 문체 스타일]\n{style_text}"},
                {"role": "user", "content": prompt},
            ]
        else:
            messages = [{"role": "user", "content": prompt}]
        ans, _ = await get_llm_response(messages)
        return ans

    import sys
    try:
        router = getattr(sys.modules.get('hybrid_router'), 'router', None)
        if router and hasattr(router, 'call'):
            result = await router.call(
                prompt=prompt,
                provider_type=provider,
                system_prompt="You are a helpful academic assistant."
            )
            if result and isinstance(result, dict):
                return result.get('content') or result.get('text', '')
    except Exception as e:
        logger.warning(f"[_call_llm] router 실패: {e}")

    import aiohttp
    api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "prompt": f"<|system|>You are a helpful academic assistant.\n<|user|>{prompt}\n<|assistant|>",
                    "n_predict": 1024,
                    "temperature": 0.7,
                }
                async with session.post("http://127.0.0.1:8080/completion", json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    data = await resp.json()
                    return data.get('content', '')
        except Exception as e2:
            return f"[LLM 호출 실패: {e2}]"

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "You are a helpful academic assistant."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 2048,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            async with session.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                data = await resp.json()
                choices = data.get('choices', [])
                if choices:
                    return choices[0].get('message', {}).get('content', '')
                return f"[API 응답 오류: {data}]"
    except Exception as e:
        return f"[DeepSeek API 호출 실패: {e}]"


async def _ingest_llm_wrapper(prompt: str) -> str:
    """사용자 현재 LLM 모드로 ingest 분류 — 모드 중립 호출"""
    from harness_agent import get_llm_response
    messages = [{"role": "user", "content": prompt}]
    ans, _ = await get_llm_response(messages)
    return ans


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    메뉴 버튼 클릭 또는 일반 질문 매핑
    - 메뉴 버튼: 직접 처리
    - 자연어 의도 분류 후 라우팅 (v8.2)
    - 일반 텍스트 / chat: harness_agent.handle_message 로 라우팅
    """
    if not await check_user(update):
        return

    # Dialectic persona 학습
    msg_text = update.message.text
    if msg_text:
        try:
            update_user_persona(msg_text, source="chat")
        except Exception:
            pass

    text = update.message.text.strip()

    # ── 자연어 의도 분류 (v8.2) ──
    _intent = None
    try:
        from natural_language_router import parse as classify_intent
        intent_info = classify_intent(text)
        if intent_info["confidence"] >= 0.5:
            _intent = intent_info["intent"]
            context.user_data["last_intent"] = intent_info
            logger.debug(
                f'[NLR] intent={intent_info["intent"]} '
                f'conf={intent_info["confidence"]:.2f} '
                f'entities={intent_info["entities"]}'
            )
    except Exception:
        pass  # 분류 실패는 메시지 처리에 영향을 주지 않음

    # ── 의도 기반 직접 라우팅 ──────────────────────────────────
    if _intent == "ask":
        context.args = text.split()
        from handlers._memory import cmd_ask
        await cmd_ask(update, context)
        return
    elif _intent == "web":
        context.args = text.split()
        from handlers._web import cmd_web
        await cmd_web(update, context)
        return
    elif _intent == "vault":
        context.args = ["search"] + text.split()
        from handlers._vault import cmd_vault
        await cmd_vault(update, context)
        return
    elif _intent == "research":
        context.args = text.split()
        from handlers._research import cmd_paper
        await cmd_paper(update, context)
        return
    # intent == "chat" 또는 None → 기존 흐름(harness_agent) 그대로 통과
    # ─────────────────────────────────────────────────────────────

    # Kanban command handling
    if text.startswith('/kanban'):
        from handlers._kanban import cmd_kanban
        await cmd_kanban(update, context)
        return

    # === 메뉴 버튼 매핑 ===
    if text == '🌙 Dreaming':
        keyboard = [[InlineKeyboardButton('✅ 실행 승인', callback_data='confirm_dreaming')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            '🌙 **Dreaming (지능형 기억/핫토픽 분배)** 작업을 시작하시겠습니까?\n'
            '이 작업은 시스템 자원을 사용하며 시간이 다소 소요될 수 있습니다.',
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        return

    if text == '📥 Ingest':
        keyboard = [[InlineKeyboardButton('✅ 실행 승인', callback_data='confirm_ingest')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            '📥 **Ingest (Clippings 위키 자동 이관)** 작업을 실행하시겠습니까?',
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        return

    if text == '📁 최근 문서':
        from handlers._file import cmd_recent
        await cmd_recent(update, context)
        return

    if text == 'ℹ️ 도움말':
        from handlers import cmd_help
        await cmd_help(update, context)
        return

    if text == '🛡️ 하네스':
        from hermes_harness import cmd_harness
        context.args = []
        await cmd_harness(update, context)
        return

    if text == '📊 진단로그':
        from hermes_harness import cmd_hstatus
        await cmd_hstatus(update, context)
        return

    if text == '🧠 메모리':
        from handlers._memory import _cmd_topmem
        await _cmd_topmem(update, context)
        return

    if text == '✍️ 논문':
        context.args = []
        from handlers._research import cmd_paper
        await cmd_paper(update, context)
        return

    if text == '🔍 보관함 진단':
        context.args = ['check']
        from handlers._vault import cmd_vault
        await cmd_vault(update, context)
        return

    # === 일반 텍스트 → harness_agent 라우팅 ===
    try:
        import importlib
        ha = importlib.import_module('harness_agent')
        await ha.handle_message(update, context)
    except Exception as e:
        logger.error(f'[harness_agent] 라우팅 실패: {e}')
        context.args = text.split()
        from handlers._memory import cmd_ask
        await cmd_ask(update, context)
