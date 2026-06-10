"""
LLM 엔진 레이어 — harness_agent.py에서 분리 (2026-06-09)
모드 관리, Circuit Breaker, 개별 엔진 호출 함수 포함
"""

import os
import asyncio
import openai
import time as _time_mod
import sys
from pathlib import Path

# config는 Scripts/ 루트에 있으므로 경로 추가
_scripts_dir = str(Path(__file__).parent.parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
import config

# ── 클라이언트 초기화 ─────────────────────────────────────
nvidia_client = None
if getattr(config, "CAPT_NVIDIA_API_KEY", ""):
    nvidia_client = openai.OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=config.CAPT_NVIDIA_API_KEY,
        timeout=60.0
    )

local_client = openai.OpenAI(
    base_url=config.LM_STUDIO_BASE_URL,
    api_key=config.LM_STUDIO_API_KEY,
    timeout=300.0
)

deepseek_client = None
if getattr(config, "DEEPSEEK_API_KEY", ""):
    deepseek_client = openai.OpenAI(
        base_url="https://api.deepseek.com",
        api_key=config.DEEPSEEK_API_KEY,
        timeout=120.0
    )

# ── 모드 정의 ────────────────────────────────────────────
MODE_QWEN14B  = "Qwen-14B"
MODE_DEEPSEEK = "DeepSeek"
MODE_NVIDIA   = "GPT OSS 120B"
ALL_MODES     = [MODE_QWEN14B, MODE_DEEPSEEK, MODE_NVIDIA]

FALLBACK_CHAIN = {
    MODE_QWEN14B:  [MODE_DEEPSEEK, MODE_NVIDIA],
    MODE_DEEPSEEK: [MODE_NVIDIA,   MODE_QWEN14B],
    MODE_NVIDIA:   [MODE_DEEPSEEK, MODE_QWEN14B],
}

MODE_LABELS = {
    MODE_QWEN14B:  "🟢 Qwen2.5 14B (로컬)",
    MODE_DEEPSEEK: "🔵 DeepSeek (API)",
    MODE_NVIDIA:   "🟣 GPT OSS 120B (NVIDIA)",
}

MODE_FILE    = "/Users/bluesea/.hermes/llm_mode.txt"
FALLBACK_FILE = "/Users/bluesea/.hermes/llm_fallback.txt"

# 자동 폴백 알림용 봇 참조 (handle_message에서 주입)
_fallback_notify_bot      = None
_fallback_notify_chat_id  = None


def get_current_mode() -> str:
    if os.path.exists(MODE_FILE):
        with open(MODE_FILE) as f:
            val = f.read().strip()
        if val == "NIM/Local":
            return MODE_QWEN14B
        if val == "NVIDIA 70B":
            return MODE_NVIDIA
        return val if val in ALL_MODES else MODE_QWEN14B
    return MODE_QWEN14B


def set_mode(mode: str) -> str:
    if mode not in ALL_MODES:
        mode = MODE_QWEN14B
    with open(MODE_FILE, "w") as f:
        f.write(mode)
    clear_auto_fallback()
    return mode


def is_auto_fallback() -> bool:
    return os.path.exists(FALLBACK_FILE)


def set_auto_fallback(reason: str = ""):
    with open(FALLBACK_FILE, "w") as f:
        f.write(reason)


def clear_auto_fallback():
    if os.path.exists(FALLBACK_FILE):
        os.remove(FALLBACK_FILE)


# ── Circuit Breaker ──────────────────────────────────────
_CIRCUIT_BREAKER: dict = {}
_CB_THRESHOLD = 3
_CB_TIMEOUT   = 60.0


def _cb_is_open(engine_key: str) -> bool:
    cb = _CIRCUIT_BREAKER.get(engine_key)
    if cb and cb["open_until"] > _time_mod.monotonic():
        return True
    return False


def _cb_record_fail(engine_key: str):
    cb = _CIRCUIT_BREAKER.setdefault(engine_key, {"fails": 0, "open_until": 0.0})
    cb["fails"] += 1
    if cb["fails"] >= _CB_THRESHOLD:
        cb["open_until"] = _time_mod.monotonic() + _CB_TIMEOUT
        print(f"⛔ [CircuitBreaker] {engine_key} — {_CB_THRESHOLD}회 연속 실패 → {_CB_TIMEOUT}초 차단")


def _cb_record_success(engine_key: str):
    if engine_key in _CIRCUIT_BREAKER:
        _CIRCUIT_BREAKER[engine_key] = {"fails": 0, "open_until": 0.0}


# ── 개별 엔진 호출 ─────────────────────────────────────────
async def _call_nvidia(messages):
    """GPT OSS 120B (NVIDIA API) 직접 호출"""
    loop = asyncio.get_running_loop()
    if not nvidia_client:
        raise Exception("NVIDIA 클라이언트 미초기화")
    print("🚀 [Engine] GPT OSS 120B (NVIDIA) 호출 중...")
    response = await asyncio.wait_for(loop.run_in_executor(None, lambda: nvidia_client.chat.completions.create(
        model="openai/gpt-oss-120b", messages=messages, temperature=0.7, max_tokens=2000
    )), timeout=60)
    return response.choices[0].message.content, "GPT OSS 120B"


async def _call_local(messages):
    """Qwen2.5 14B (llama-server) completions API"""
    import re
    loop = asyncio.get_running_loop()
    prompt_parts = []
    for m in messages:
        role    = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            prompt_parts.append(f"<start_of_turn>system\n{content}<end_of_turn>")
        elif role == "assistant":
            prompt_parts.append(f"<start_of_turn>model\n{content}<end_of_turn>")
        else:
            prompt_parts.append(f"<start_of_turn>user\n{content}<end_of_turn>")
    prompt_parts.append("<start_of_turn>model\n")
    prompt = "\n".join(prompt_parts)

    print("💻 [Engine] Qwen2.5 14B (로컬) 호출 중... (chat template bypass)")
    response = await asyncio.wait_for(loop.run_in_executor(None, lambda: local_client.completions.create(
        model="local-model",
        prompt=prompt,
        temperature=0.7,
        frequency_penalty=0.5,
        presence_penalty=0.5,
        extra_body={"repeat_penalty": 1.15},
        max_tokens=3500,
        stop=["<end_of_turn>", "<eos>", "<|im_end|>"]
    )), timeout=120)
    text = response.choices[0].text.strip()
    text = re.sub(r"<\|?[^>]+\|?>", "", text).strip()
    text = re.sub(r"^(thought|thinking|reasoning|internal)\s*\n", "", text, flags=re.MULTILINE).strip()
    if not text:
        raise ValueError(f"Qwen returned empty response. Raw: {repr(response.choices[0].text[:200])}")
    return text, "Qwen2.5 14B (로컬)"


async def _call_deepseek(messages):
    """DeepSeek 직접 호출"""
    loop = asyncio.get_running_loop()
    if not deepseek_client:
        raise Exception("DeepSeek 클라이언트 미초기화")
    print("🚀 [Engine] DeepSeek 호출 중...")
    response = await asyncio.wait_for(loop.run_in_executor(None, lambda: deepseek_client.chat.completions.create(
        model="deepseek-chat", messages=messages, temperature=0.7, max_tokens=2000
    )), timeout=45)
    return response.choices[0].message.content, "DeepSeek"


_ENGINE_MAP = {
    MODE_QWEN14B:  ("Qwen2.5 14B (로컬)",    _call_local),
    MODE_DEEPSEEK: ("DeepSeek (API)",          _call_deepseek),
    MODE_NVIDIA:   ("GPT OSS 120B (NVIDIA)",   _call_nvidia),
}

_FALLBACK_ORDER = [
    ("DeepSeek",     _call_deepseek),
    ("GPT OSS 120B", _call_nvidia),
    ("Qwen2.5 14B",  _call_local),
]


async def call_primary_with_fallback(messages, primary_mode=None):
    """프라이머리 모드 호출 → 실패 시 자동 폴백 체인"""
    global _fallback_notify_bot, _fallback_notify_chat_id
    if primary_mode is None:
        primary_mode = get_current_mode()
    primary_label, primary_func = _ENGINE_MAP.get(primary_mode, _ENGINE_MAP[MODE_QWEN14B])

    cb_key = primary_mode
    if _cb_is_open(cb_key):
        remaining = int(_CIRCUIT_BREAKER[cb_key]["open_until"] - _time_mod.monotonic())
        print(f"⛔ [CircuitBreaker] {primary_label} 차단 중 (잔여 {remaining}초) → 즉시 폴백")
        err_msg = "회로 차단"
    else:
        try:
            result = await primary_func(messages)
            _cb_record_success(cb_key)
            if is_auto_fallback():
                clear_auto_fallback()
                print(f"✅ [AutoFallback] {primary_label} 복구 확인 → {MODE_LABELS[primary_mode]} 모드로 자동 복귀")
                if _fallback_notify_bot and _fallback_notify_chat_id:
                    try:
                        await _fallback_notify_bot.send_message(
                            chat_id=_fallback_notify_chat_id,
                            text=f"✅ **[자동 복구]** {primary_label}이(가) 다시 정상 작동합니다.\n→ **{MODE_LABELS[primary_mode]} 모드로 자동 복귀**했습니다.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            return result
        except Exception as e:
            err_msg = str(e)
            print(f"⚠️ [Fallback] {primary_label} 실패: {err_msg[:100]}")
            _cb_record_fail(cb_key)

    if not is_auto_fallback():
        set_auto_fallback(f"{primary_label} 실패")
        if _fallback_notify_bot and _fallback_notify_chat_id:
            try:
                await _fallback_notify_bot.send_message(
                    chat_id=_fallback_notify_chat_id,
                    text=f"⚠️ **[자동 폴백 발동]** {primary_label}이(가) 응답하지 않습니다.\n→ 폴백 체인을 통해 자동 전환합니다.\n\n원인: `{err_msg[:200]}`",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    for fallback_label, fallback_func in _FALLBACK_ORDER:
        if fallback_label.split(" ")[0] == primary_label.split(" ")[0]:
            continue
        fb_key = fallback_label
        if _cb_is_open(fb_key):
            print(f"⛔ [CircuitBreaker] 폴백 {fallback_label} 도 차단 중 → 건너뜀")
            continue
        try:
            print(f"🔄 [Fallback] {fallback_label} 시도 중...")
            result = await fallback_func(messages)
            actual_text = result[0].strip() if result else ""
            if actual_text and not actual_text.startswith("❌"):
                _cb_record_success(fb_key)
                return (result[0], f"{fallback_label} (자동폴백)")
        except Exception as e2:
            print(f"⚠️ [Fallback] {fallback_label} 실패: {str(e2)[:100]}")
            _cb_record_fail(fb_key)
            continue

    return "❌ 모든 엔진(Qwen2.5, DeepSeek, GPT OSS 120B)이 응답하지 않습니다. 잠시 후 다시 시도해 주세요.", "Error"
