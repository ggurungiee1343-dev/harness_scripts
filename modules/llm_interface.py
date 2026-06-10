# modules/llm_interface.py
# 목적: AI Gateway 패턴 이식 - 재시도, 지수 백오프, 에러 캡슐화가 통합된 단일 LLM 창구

import asyncio
import aiohttp
import json
import os
import time
from typing import Optional


class LLMInterface:
    def __init__(self):
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.nim_key       = os.getenv("ANTIGRAVITY_NVIDIA_API_KEY", "")
        self.gemma_url     = os.getenv("GEMMA_ENDPOINT", "http://localhost:8080/completion")
        self.default_provider = os.getenv("DEFAULT_LLM_PROVIDER", "deepseek")

    # === NVIDIA API CONNECTIVITY PATCH START ===
    # [NVIDIA NIM & DeepSeek Tool Calling Support]
    # 이 블록을 삭제하고, 하단에 주석 처리된 기존 함수들을 다시 살려주시면 원래 상태로 완벽히 복원됩니다.
    async def complete(
        self,
        prompt,  # str or list
        provider: Optional[str] = None,
        max_tokens: int = 1000
    ) -> str:
        provider = provider or self.default_provider
        
        # prompt가 문자열이면 포맷팅하고, 리스트이면 전체 메시지 컨텍스트(messages)로 간주합니다.
        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [{"role": "user", "content": prompt}]

        if provider == "deepseek":
            return await self._call_deepseek(messages, max_tokens)
        elif provider == "gemma4":
            return await self._call_gemma4(messages, max_tokens)
        elif provider == "nim":
            return await self._call_nim(messages, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    # ── AI Gateway 핵심: 지수 백오프 재시도 헬퍼 ──────────────────
    async def _request_with_retry(self, session, url, headers, payload, provider_name, max_retries=3) -> dict:
        fallback_delay = 1.0
        for attempt in range(max_retries):
            try:
                async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                    if resp.status == 429:  # Rate Limit 대응
                        print(f"⏳ [AI Gateway] {provider_name} Rate Limit 감지. {fallback_delay}초 후 재시도...")
                        await asyncio.sleep(fallback_delay)
                        fallback_delay *= 2
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == max_retries - 1:
                    print(f"❌ [AI Gateway] {provider_name} 최종 시도(#{attempt+1}) 실패: {e}")
                    raise e
                print(f"⚠️ [AI Gateway] {provider_name} 연결 제한/오류(#{attempt+1}). {fallback_delay}초 후 재시도...")
                await asyncio.sleep(fallback_delay)
                fallback_delay *= 2

    # ── 내부 구현 ──────────────────────────────────────────────
    async def _call_deepseek(self, messages: list, max_tokens: int) -> str:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.deepseek_key}", "Content-Type": "application/json"}
        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "max_tokens": max_tokens
        }
        async with aiohttp.ClientSession() as session:
            data = await self._request_with_retry(session, url, headers, payload, "DeepSeek")
            return data["choices"][0]["message"]["content"]

    async def _call_gemma4(self, messages: list, max_tokens: int) -> str:
        """Qwen3.6 (로컬) completions API via GEMMA_ENDPOINT"""
        prompt_text = ""
        for m in messages:
            prompt_text += f"<{m.get('role', 'user')}>\n{m.get('content', '')}\n"
        
        payload = {"prompt": prompt_text, "n_predict": max_tokens}
        async with aiohttp.ClientSession() as session:
            async with session.post(self.gemma_url, json=payload, timeout=15) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get("content", "")

    async def _call_nim(self, messages: list, max_tokens: int) -> str:
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.nim_key}", "Content-Type": "application/json"}
        payload = {
            "model": "openai/gpt-oss-120b",
            "messages": messages,
            "max_tokens": max_tokens
        }
        async with aiohttp.ClientSession() as session:
            data = await self._request_with_retry(session, url, headers, payload, "NVIDIA-NIM")
            msg = data["choices"][0]["message"]
            return msg.get("content") or msg.get("reasoning") or ""
    # === NVIDIA API CONNECTIVITY PATCH END ===

    # ==========================================
    # 아래는 복원용 주석 코드 (원본 상태)
    # 복원하려면 위 PATCH 블록을 삭제하고 아래 주석을 해제하세요.
    # ==========================================
    # async def complete(
    #     self,
    #     prompt: str,
    #     provider: Optional[str] = None,
    #     max_tokens: int = 1000
    # ) -> str:
    #     provider = provider or self.default_provider
    #     if provider == "deepseek":
    #         return await self._call_deepseek(prompt, max_tokens)
    #     elif provider == "gemma4":
    #         return await self._call_gemma4(prompt, max_tokens)
    #     elif provider == "nim":
    #         return await self._call_nim(prompt, max_tokens)
    #     else:
    #         raise ValueError(f"Unknown provider: {provider}")
    # 
    # # ── AI Gateway 핵심: 지수 백오프 재시도 헬퍼 ──────────────────
    # async def _request_with_retry(self, session, url, headers, payload, provider_name, max_retries=3) -> dict:
    #     fallback_delay = 1.0
    #     for attempt in range(max_retries):
    #         try:
    #             async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
    #                 if resp.status == 429:  # Rate Limit 대응
    #                     print(f"⏳ [AI Gateway] {provider_name} Rate Limit 감지. {fallback_delay}초 후 재시도...")
    #                     await asyncio.sleep(fallback_delay)
    #                     fallback_delay *= 2
    #                     continue
    #                 resp.raise_for_status()
    #                 return await resp.json()
    #         except (aiohttp.ClientError, asyncio.TimeoutError) as e:
    #             if attempt == max_retries - 1:
    #                 print(f"❌ [AI Gateway] {provider_name} 최종 시도(#{attempt+1}) 실패: {e}")
    #                 raise e
    #             print(f"⚠️ [AI Gateway] {provider_name} 연결 제한/오류(#{attempt+1}). {fallback_delay}초 후 재시도...")
    #             await asyncio.sleep(fallback_delay)
    #             fallback_delay *= 2
    # 
    # # ── 내부 구현 ──────────────────────────────────────────────
    # async def _call_deepseek(self, prompt: str, max_tokens: int) -> str:
    #     url = "https://api.deepseek.com/v1/chat/completions"
    #     headers = {"Authorization": f"Bearer {self.deepseek_key}", "Content-Type": "application/json"}
    #     payload = {
    #         "model": "deepseek-chat",
    #         "messages": [{"role": "user", "content": prompt}],
    #         "max_tokens": max_tokens
    #     }
    #     async with aiohttp.ClientSession() as session:
    #         data = await self._request_with_retry(session, url, headers, payload, "DeepSeek")
    #         return data["choices"][0]["message"]["content"]
    # 
    # async def _call_gemma4(self, prompt: str, max_tokens: int) -> str:
    #     payload = {"prompt": prompt, "n_predict": max_tokens}
    #     async with aiohttp.ClientSession() as session:
    #         async with session.post(self.gemma_url, json=payload, timeout=15) as resp:
    #             resp.raise_for_status()
    #             data = await resp.json()
    #             return data.get("content", "")
    # 
    # async def _call_nim(self, prompt: str, max_tokens: int) -> str:
    #     url = "https://integrate.api.nvidia.com/v1/chat/completions"
    #     headers = {"Authorization": f"Bearer {self.nim_key}", "Content-Type": "application/json"}
    #     payload = {
    #         "model": "minimaxai/minimax-m2.7",
    #         "messages": [{"role": "user", "content": prompt}],
    #         "max_tokens": max_tokens
    #     }
    #     async with aiohttp.ClientSession() as session:
    #         data = await self._request_with_retry(session, url, headers, payload, "NVIDIA-NIM")
    #         return data["choices"][0]["message"]["content"]
