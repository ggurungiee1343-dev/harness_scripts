"""
context_assembler_v2.py — 컨텍스트 조립 엔진 개선판

근거:
  TAOUP Rule of Simplicity:
    "Design for simplicity; add complexity only where you must"
    현재 assemble_context()가 7개 소스를 순서대로 조립하다가
    하나 실패하면 다음으로 넘어가는 구조 → 버그가 glue 코드에 집중됨

  TAOUP Rule of Modularity:
    "Bugs tend to collect in glue"
    → 각 소스를 독립 함수로 분리, assemble은 단순 조합만

  Exceeds AI — 검증 오버헤드:
    시니어 개발자가 19% 느려지는 이유 = AI 생성 코드 검증 오버헤드
    → context가 과도하게 크면 LLM이 느려지고 응답 품질도 저하
    → 카테고리 필터로 context 크기를 제어

  Pike Rule 1 & 2:
    측정 없이 최적화하지 말 것
    → assemble_context()에 소요 시간 측정 추가

개선 요약:
  기존: 7개 소스를 항상 전부 조립 (wiki + style + history15 + L2/L3 + Resolver + briefing + 날씨)
  개선: 3개 필수 + 키워드 감지 시 선택적 추가 (Rule of Simplicity)
        각 블록이 독립적으로 실패/성공 (Rule of Robustness)
        소요 시간 측정 로그 (Pike Rule 2)

설치 위치:
  ~/Applications/Mjauto/Scripts/modules/context_assembler_v2.py

적용 방법:
  harness_agent.py에서
    from modules.context_assembler import assemble_context
  →
    from modules.context_assembler_v2 import assemble_context
  로 교체 (1줄만 바꾸면 됨 — Rule of Composition)
"""

import time
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# ── 트리거 키워드 (Rule of Representation: 조건을 데이터로) ────────────────────
TRIGGERS = {
    "weather": ["날씨", "기온", "비", "눈", "weather", "temperature"],
    "system":  ["하네스", "hermes", "시스템", "스크립트", "메모리", "모듈", "config"],
    "stock":   ["주식", "스캔", "삼돌이", "종목", "매수", "stock", "screener"],
    "wiki":    ["위키", "논문", "가이드", "설명서", "wiki", "paper"],
}

def _detect_triggers(user_text: str) -> set[str]:
    """키워드 테이블로 필요한 컨텍스트 블록 결정. 로직 없이 데이터로."""
    text_lower = user_text.lower()
    active = set()
    for name, keywords in TRIGGERS.items():
        if any(kw in text_lower for kw in keywords):
            active.add(name)
    return active


# ── 독립 컨텍스트 블록 (각각 독립 실패 가능 — Rule of Robustness) ─────────────

def _block_system_prompt(sys_prompt: str) -> dict | None:
    if not sys_prompt:
        return None
    return {"role": "system", "content": sys_prompt}


def _block_history(history: list, keep_recent: int = 10) -> list[dict]:
    """최근 N턴만. 기존 15턴 → 10턴으로 축소 (Rule of Parsimony)"""
    if not history:
        return []
    recent = history[-keep_recent * 2:]  # user/assistant 쌍
    return [{"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in recent if m.get("content")]


def _block_l3_memory(
    user_text: str,
    l3_path: str,
    triggers: set[str],
    top_k: int = 5,
) -> dict | None:
    """
    L3 시맨틱 메모리. 카테고리 필터로 관련 패턴만 로드.
    Rule of Representation: 카테고리 구조가 탐색 로직을 단순하게 만듦
    """
    try:
        from modules.memory_schema import SemanticMemory, classify_category
        l3 = SemanticMemory(l3_path)
        if len(l3) == 0:
            return None

        # 카테고리 필터 (전체 탐색 대신 관련 카테고리만)
        cats = [classify_category(user_text)]
        if "stock" in triggers:  cats.append("stock")
        if "system" in triggers: cats.append("system")
        if "wiki" in triggers:   cats.extend(["maritime_law", "research"])
        cats = list(set(cats))

        patterns = l3.recall(user_text, categories=cats, top_k=top_k)
        if not patterns:
            return None

        content = "📚 관련 지식:\n" + "\n".join(f"• {p.pattern}" for p in patterns)
        return {"role": "system", "content": content}
    except Exception as e:
        logger.debug(f"L3 블록 실패 (무시): {e}")
        return None


def _block_briefing(briefing_path: str) -> dict | None:
    """시스템 팩트. system 키워드 감지 시에만 주입."""
    try:
        p = Path(briefing_path)
        if not p.exists():
            return None
        content = p.read_text(encoding="utf-8")[:2000]  # 최대 2000자
        return {"role": "system", "content": f"[시스템 팩트]\n{content}"}
    except Exception as e:
        logger.debug(f"briefing 블록 실패 (무시): {e}")
        return None


def _block_weather(city: str = "울산") -> dict | None:
    """날씨 키워드 감지 시에만 실행. 실패해도 전체에 영향 없음."""
    try:
        import urllib.request
        from bs4 import BeautifulSoup
        url = f"https://search.naver.com/search.naver?query={city}+날씨"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            soup = BeautifulSoup(resp.read(), "html.parser")
        temp = soup.select_one(".temperature_text strong")
        desc = soup.select_one(".weather_main")
        if temp and desc:
            return {"role": "system",
                    "content": f"[날씨] {city}: {temp.text.strip()} {desc.text.strip()}"}
    except Exception as e:
        logger.debug(f"날씨 블록 실패 (무시): {e}")
    return None


def _block_wiki(wiki_obj, user_text: str) -> dict | None:
    """Wiki 검색. wiki 키워드 감지 시에만 실행."""
    try:
        result = wiki_obj.search(user_text, top_k=3) if hasattr(wiki_obj, "search") else None
        if result:
            return {"role": "system", "content": f"[Wiki]\n{result}"}
    except Exception as e:
        logger.debug(f"Wiki 블록 실패 (무시): {e}")
    return None


# ── 메인 조립 함수 ─────────────────────────────────────────────────────────────

async def assemble_context(
    sys_prompt: str,
    user_text: str,
    history: list,
    wiki=None,
    memory=None,           # 하위 호환성 유지 (사용 안 함)
    config: dict = None,
    update_progress_fn: Callable = None,
    # 경로
    l3_path: str = None,
    briefing_path: str = None,
) -> list[dict]:
    """
    LLM에 전달할 messages 조립.

    개선 원칙:
      1. 필수 블록(3개)만 항상 포함
      2. 선택 블록(4개)은 키워드 감지 시에만 추가
      3. 각 블록 독립 실패 가능
      4. 소요 시간 측정 (Pike Rule 2)

    기존 대비:
      항상 7블록 → 키워드 따라 3~7블록
      history 15턴 → 10턴
      L2 전체 탐색 → L3 카테고리 필터
    """
    t0 = time.perf_counter()
    messages: list[dict] = []

    # ── 키워드 감지 ──────────────────────────────────────────────────────────────
    triggers = _detect_triggers(user_text)

    # ── 필수 블록 (항상 포함) ─────────────────────────────────────────────────────
    if sys_prompt:
        blk = _block_system_prompt(sys_prompt)
        if blk: messages.append(blk)

    # ── 선택 블록 (키워드 감지 시) ───────────────────────────────────────────────
    if "system" in triggers and briefing_path:
        blk = _block_briefing(briefing_path)
        if blk: messages.append(blk)

    l3_path = l3_path or str(
        Path.home() / "Applications" / "Mjauto" / "Scripts" / "modules" / "semantic_memory.json"
    )
    if True:  # L3는 항상 시도 (카테고리 필터로 가볍게)
        blk = _block_l3_memory(user_text, l3_path, triggers, top_k=5)
        if blk: messages.append(blk)

    if "wiki" in triggers and wiki:
        blk = _block_wiki(wiki, user_text)
        if blk: messages.append(blk)

    if "weather" in triggers:
        if update_progress_fn: update_progress_fn("날씨 확인 중...")
        blk = _block_weather()
        if blk: messages.append(blk)

    # ── 히스토리 (필수, 마지막에 추가) ───────────────────────────────────────────
    messages.extend(_block_history(history, keep_recent=10))

    # ── 측정 로그 (Pike Rule 2) ───────────────────────────────────────────────────
    elapsed = time.perf_counter() - t0
    block_count = len(messages)
    logger.info(
        f"[context_assembler_v2] {block_count}블록 조립 | "
        f"triggers={triggers or '없음'} | {elapsed*1000:.0f}ms"
    )

    return messages


# ── 마이그레이션 헬퍼 ─────────────────────────────────────────────────────────
def patch_harness_agent(harness_path: str = None) -> str:
    """
    harness_agent.py의 import 1줄을 교체하는 패치 지시문 반환.
    실제 파일은 건드리지 않음 (안전).
    """
    return """
harness_agent.py 수정 방법 (1줄):

변경 전:
  from modules.context_assembler import assemble_context

변경 후:
  from modules.context_assembler_v2 import assemble_context

이게 전부입니다.
기존 assemble_context() 호출부는 그대로 유지됩니다.
(인터페이스가 동일하므로 — Rule of Composition)
"""


if __name__ == "__main__":
    import asyncio

    async def _test():
        msgs = await assemble_context(
            sys_prompt="You are Hermes, a personal AI assistant.",
            user_text="오늘 날씨 어때요? 그리고 하네스 상태 알려줘",
            history=[
                {"role": "user", "content": "안녕"},
                {"role": "assistant", "content": "안녕하세요!"},
            ],
        )
        print(f"\n조립된 메시지: {len(msgs)}블록")
        for m in msgs:
            role = m.get("role")
            content = m.get("content", "")[:80]
            print(f"  [{role}] {content}...")
        print(patch_harness_agent())

    asyncio.run(_test())
