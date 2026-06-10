"""
hybrid_router.py — Action Realization Layer v2.7 (v9.2 Multi-Model Load Balancing)
===================================================================================
작업의 민감도를 분석하여 로컬 모델(보안)과 DeepSeek/NVIDIA(추론)로 자동 라우팅합니다.
v9.2: ModelLoadBalancer 통합 — 응답시간/성공률 기반 최적 모델 선택.
"""

import re
import asyncio
import os
import time
from modules.llm_interface import LLMInterface
from modules.load_balancer import ModelLoadBalancer


class HybridRouter:
    """Action Realization Layer: 작업을 분석하여 적절한 LLM으로 라우팅

    [Hermes 2] 당분간 nim(minimaxai/minimax-m2.7)만 사용.
    call_deepseek / call_gemma4 는 dead code 로 남겨둠 (향후 재활용 가능).
    route_and_execute() 의 LoadBalancer 는 항상 "nvidia" 반환으로 고정.
    [Hermes 1] 텔레그램 하단 모드 전환 기능으로 gemma4/deepseek/nvidia 모두 사용 가능.
    """

    def __init__(self, local_llm_func, api_llm_func, provider_funcs: dict = None):
        self.local_llm_func = local_llm_func
        self.api_llm_func = api_llm_func
        self._provider_funcs = provider_funcs or {}
        self.llm_interface = LLMInterface()
        # v9.2: Model Load Balancer
        self.load_balancer = ModelLoadBalancer()
        self._load_balancer_enabled = True

        self.sensitive_patterns = [
            r"미발표", r"비공개", r"password", r"secret",
            r"private", r"내 연구", r"우리 논문"
        ]

    # === HYBRID ROUTER PATCH START ===
    # [Context Preservation Patch] System Prompt(도구 호출 지침)와 대화 내역 전체를 API에 전송하도록 수정.
    # 원래 코드로 돌아가려면 이 블록을 삭제하고, 하단에 주석 처리된 기존 함수들을 다시 살려주세요.
    async def call_deepseek(self, messages: list) -> tuple:
        """DeepSeek API 호출 + LoadBalancer 성능 기록"""
        start = time.time()
        try:
            # 단일 prompt 문자열이 아닌 messages 리스트 전체를 전달
            res = await self.llm_interface.complete(messages, provider="deepseek")
            elapsed_ms = (time.time() - start) * 1000
            await self.load_balancer.record_model_performance("deepseek", elapsed_ms, True)
            return res, "DeepSeek"
        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            await self.load_balancer.record_model_performance("deepseek", elapsed_ms, False)
            print(f"💥 [v9.2 LB] DeepSeek 가용성 붕괴 -> NVIDIA(NIM)로 긴급 페일오버")
            return await self.call_nim(messages)

    async def call_gemma4(self, messages: list) -> tuple:
        """로컬 Gemma4 호출 + LoadBalancer 성능 기록"""
        start = time.time()
        try:
            res = await self.llm_interface.complete(messages, provider="gemma4")
            elapsed_ms = (time.time() - start) * 1000
            await self.load_balancer.record_model_performance("gemma4", elapsed_ms, True)
            return res, "Gemma4"
        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            await self.load_balancer.record_model_performance("gemma4", elapsed_ms, False)
            print(f"💥 [v9.2 LB] 로컬 Gemma4 인퍼런스 서버 다운 -> NIM 긴급 페일오버")
            return await self.call_nim(messages)

    async def call_nim(self, messages: list) -> tuple:
        """NVIDIA NIM 호출 + LoadBalancer 성능 기록"""
        start = time.time()
        try:
            # 단일 prompt 문자열이 아닌 messages 리스트 전체를 전달
            res = await self.llm_interface.complete(messages, provider="nim")
            elapsed_ms = (time.time() - start) * 1000
            await self.load_balancer.record_model_performance("nvidia", elapsed_ms, True)
            return res, "NVIDIA 70B"
        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            await self.load_balancer.record_model_performance("nvidia", elapsed_ms, False)
            print(f"💥 [v9.2 LB] 전 금융/외부 API 망 차단 -> 레거시 로컬 핸들러 최종 백업")
            if self.api_llm_func:
                try: return await self.api_llm_func(messages)
                except Exception: pass
            return await self.local_llm_func(messages)
    # === HYBRID ROUTER PATCH END ===

    # ==========================================
    # 아래는 복원용 주석 코드 (원본 상태)
    # 복원하려면 위 PATCH 블록을 삭제하고 아래 주석을 해제하세요.
    # ==========================================
    # async def call_deepseek(self, messages: list) -> tuple:
    #     """DeepSeek API 호출 + LoadBalancer 성능 기록"""
    #     prompt = messages[-1].get("content", "") if messages else ""
    #     start = time.time()
    #     try:
    #         res = await self.llm_interface.complete(prompt, provider="deepseek")
    #         elapsed_ms = (time.time() - start) * 1000
    #         await self.load_balancer.record_model_performance("deepseek", elapsed_ms, True)
    #         return res, "DeepSeek"
    #     except Exception as e:
    #         elapsed_ms = (time.time() - start) * 1000
    #         await self.load_balancer.record_model_performance("deepseek", elapsed_ms, False)
    #         print(f"💥 [v9.2 LB] DeepSeek 가용성 붕괴 -> NVIDIA(NIM)로 긴급 페일오버")
    #         return await self.call_nim(messages)
    # 
    # async def call_gemma4(self, messages: list) -> tuple:
    #     """로컬 Gemma4 호출 + LoadBalancer 성능 기록"""
    #     prompt = messages[-1].get("content", "") if messages else ""
    #     start = time.time()
    #     try:
    #         res = await self.llm_interface.complete(prompt, provider="gemma4")
    #         elapsed_ms = (time.time() - start) * 1000
    #         await self.load_balancer.record_model_performance("gemma4", elapsed_ms, True)
    #         return res, "Gemma4"
    #     except Exception as e:
    #         elapsed_ms = (time.time() - start) * 1000
    #         await self.load_balancer.record_model_performance("gemma4", elapsed_ms, False)
    #         print(f"💥 [v9.2 LB] 로컬 Gemma4 인퍼런스 서버 다운 -> NIM 긴급 페일오버")
    #         return await self.call_nim(messages)
    # 
    # async def call_nim(self, messages: list) -> tuple:
    #     """NVIDIA NIM 호출 + LoadBalancer 성능 기록"""
    #     prompt = messages[-1].get("content", "") if messages else ""
    #     start = time.time()
    #     try:
    #         res = await self.llm_interface.complete(prompt, provider="nim")
    #         elapsed_ms = (time.time() - start) * 1000
    #         await self.load_balancer.record_model_performance("nvidia", elapsed_ms, True)
    #         return res, "NVIDIA 70B"
    #     except Exception as e:
    #         elapsed_ms = (time.time() - start) * 1000
    #         await self.load_balancer.record_model_performance("nvidia", elapsed_ms, False)
    #         print(f"💥 [v9.2 LB] 전 금융/외부 API 망 차단 -> 레거시 로컬 핸들러 최종 백업")
    #         if self.api_llm_func:
    #             try: return await self.api_llm_func(messages)
    #             except Exception: pass
    #         return await self.local_llm_func(messages)

    async def _call_llm(self, prompt: str, provider: str = None) -> str:
        if provider is not None:
            try: return await self.llm_interface.complete(prompt, provider=provider.lower())
            except Exception: pass

        messages = [{"role": "user", "content": prompt}]
        result = await self.route_and_execute(messages)
        return result[0] if isinstance(result, tuple) else result

    def is_sensitive(self, text: str) -> bool:
        for pattern in self.sensitive_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    async def route_and_execute(self, messages, force_local=False, force_primary=None):
        _CAVEMAN_FILE = os.path.expanduser("~/.hermes/caveman.txt")
        if os.path.exists(_CAVEMAN_FILE):
            try:
                if open(_CAVEMAN_FILE).read().strip() == "on":
                    from harness_agent import _CAVEMAN_SYS_PROMPT
                    for msg in messages:
                        if msg.get("role") == "system":
                            msg["content"] = _CAVEMAN_SYS_PROMPT
                            break
            except Exception: pass

        if force_primary:
            return await self.local_llm_func(messages, force_primary)
        if force_local:
            return await self.local_llm_func(messages)

        full_text = " ".join([m.get("content", "") for m in messages])

        if self.is_sensitive(full_text):
            print("🛡️ [v9.2 하이브리드 라우터] 민감 정보 감지 -> Gemma4 분기 가동")
            return await self.call_gemma4(messages)
        else:
            # v9.2: 일반 컨텍스트 — LoadBalancer로 최적 모델 선택
            if self._load_balancer_enabled:
                best_model = await self.load_balancer.select_best_model()
                print(f"🌐 [v9.2 LoadBalancer] 최적 모델 선택: {best_model}")
                if best_model == "gemma4":
                    return await self.call_gemma4(messages)
                elif best_model == "deepseek":
                    return await self.call_deepseek(messages)
                elif best_model == "nvidia":
                    return await self.call_nim(messages)
                else:
                    return await self.call_deepseek(messages)
            else:
                print("🌐 [v9.2 하이브리드 라우터] 일반 컨텍스트 -> call_deepseek 분기 가동")
                return await self.call_deepseek(messages)


class DummyRouter:
    def send_completion(self, messages): return 'LLM router dummy response', 'Dummy'
router = DummyRouter()

def _patch_send_completion(router_instance):
    async def _async_send(prompt):
        messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
        return await router_instance.route_and_execute(messages)
    def send_completion(prompt):
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(_async_send(prompt)))
                return future.result()
        except RuntimeError:
            return asyncio.run(_async_send(prompt))
    router_instance.send_completion = send_completion
    return router_instance

def patch_router(real_hybrid_router):
    global router
    router = _patch_send_completion(real_hybrid_router)
