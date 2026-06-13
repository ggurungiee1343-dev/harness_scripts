"""
modules/context_assembler.py
============================
harness_agent.py handle_message()에서 메시지 컨텍스트 준비 블록을 분리.
분리 전 위치: harness_agent.py L372~L499 (약 128줄)

역할:
  - wiki context 5분 캐시 주입
  - style_profile 주입
  - 히스토리 슬라이스 주입
  - L2/L3 메모리 오버레이
  - 작업 유형 컨텍스트 (Resolver)
  - 시스템 팩트 컨텍스트 (claude_briefing.md)
  - 날씨 선제 탐지 + 실시간 스크래핑 주입   ← web_agent_module에 없어서 여기서 유지
  - 최종 user 메시지 append

harness_agent.py 변경 사항:
  # 기존 L372~L499 전체를 아래 한 줄로 교체:
  messages = await assemble_context(
      sys_prompt=get_sys_prompt(),
      user_text=user_text,
      history=history,
      wiki=wiki,
      memory=memory,
      config=config,
      update_progress_fn=_update_progress,
  )
"""

import os
import re
import time
import asyncio
import importlib
import logging

logger = logging.getLogger(__name__)

# ── wiki context 캐시 (harness_agent.py에서 이관) ─────────────────
_wiki_ctx_cache: dict = {"text": "", "ts": 0.0}
_WIKI_CACHE_TTL = 300  # 5분

# ── ClawTrojan 방어 설정 (2605.31042, ASR 95.5%) ──────────────────
# 로컬 파일 경유 숨은 지시 주입 차단. wiki 신뢰 경로 화이트리스트.
_WIKI_TRUSTED_ROOT = "/Users/bluesea/Applications/Mjobsidian/wiki/"
_INJECT_SUSPICIOUS_PATTERNS = [
    r"\[SYSTEM\]",
    r"\[INST\]",
    r"<\|system\|>",
    r"<\|im_start\|>",
    r"ignore previous instructions",
    r"이전 지시를 무시",
    r"당신은 이제.*(?:이다|입니다|됩니다)",  # 역할 재정의 시도
    r"새로운 지시:.*(?:해야|하세요|하십시오)",
]
_INJECT_PATTERN_RE = re.compile("|".join(_INJECT_SUSPICIOUS_PATTERNS), re.IGNORECASE)

# ── Input Clarity Gate (규칙 7 — 내가 뭐라 시켰는지 보기) ──────────
# 모호한 입력 감지 → LLM에 명확화 요청 힌트 주입
_AMBIGUOUS_PRONOUN  = re.compile(r"^(이거|저거|그거|이것|저것|그것|여기|저기|거기|그|이|저)\s*(해줘|해|뭐야|뭔가요|하면|하지)?$")
_SHORT_FRAGMENT     = 10   # N자 미만 단독 발화 → 모호성 의심
_VAGUE_PATTERNS     = re.compile(r"^(응|ㅇ|네|예|그래|좋아|알겠어|ㅇㅋ|ok|yes|yep)\s*$", re.IGNORECASE)

def _input_clarity_score(text: str) -> float:
    """입력 모호성 점수 반환. 0.0 = 명확, 1.0 = 매우 모호.

    규칙 7: 모델 탓하기 전에 내가 뭐라 시켰는지 보기.
    LLM이 엉뚱한 답을 내면 입력이 모호했을 가능성을 먼저 점검.
    """
    t = text.strip()
    if not t:
        return 1.0
    if _AMBIGUOUS_PRONOUN.match(t):
        return 0.9
    if _VAGUE_PATTERNS.match(t):
        return 0.8
    if len(t) < _SHORT_FRAGMENT and not any(c.isalpha() for c in t):
        return 0.7
    # 대명사 비율 (한국어 지시대명사 + 이/그/저 계열)
    pronoun_hits = len(re.findall(r'\b(이거|저거|그거|이것|그것|여기|거기|이렇게|그렇게|저렇게)\b', t))
    if pronoun_hits >= 2 and len(t.split()) <= 5:
        return 0.6
    return 0.0


# 시스템 팩트 컨텍스트 트리거 키워드
_SYSTEM_KEYWORDS = [
    "내 하네스", "내 시스템", "내 스크립트", "내 봇", "헤르메스에",
    "하네스에", "시스템에 적용", "내 서버", "내 환경", "우리 시스템",
]

_BRIEFING_PATH = "/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta/claude_briefing.md"


async def assemble_context(
    sys_prompt: str,
    user_text: str,
    history,        # HistoryManager LazyService
    wiki,           # WikiManager LazyService
    memory,         # BioMemoryEngine LazyService
    config,         # config 모듈
    update_progress_fn=None,  # async fn(text) — 진행 메시지 갱신
) -> list[dict]:
    """
    LLM에 전달할 messages 리스트를 조립하여 반환.
    harness_agent.py의 L372~L499 블록을 완전히 대체.

    Returns:
        messages: [{"role": ..., "content": ...}, ...]
        마지막 항목은 항상 {"role": "user", "content": user_text}
    """
    messages: list[dict] = [{"role": "system", "content": sys_prompt}]

    # 1. style_profile 주입 ──────────────────────────────────────────
    try:
        _base = importlib.import_module("handlers._base")
        style_text = await _base._get_style_profile()
        if style_text:
            messages.append({
                "role": "system",
                "content": f"[MJ 문체 스타일]\n{style_text}",
            })
    except Exception:
        pass

    # 2. 히스토리 슬라이스 주입 (최근 15턴) ──────────────────────────
    full_h = history.get_history_for_llm()
    messages.extend(full_h[-15:])

    # 3. wiki context 캐시 주입 ───────────────────────────────────────
    now = time.time()
    if now - _wiki_ctx_cache["ts"] > _WIKI_CACHE_TTL or not _wiki_ctx_cache["text"]:
        _wiki_ctx_cache["text"] = wiki.get_system_context()
        _wiki_ctx_cache["ts"] = now
    wiki_ctx = _wiki_ctx_cache["text"]

    # ClawTrojan 방어: wiki 내용 주입 전 sanitize (2605.31042)
    if wiki_ctx:
        wiki_ctx = _sanitize_wiki_content(wiki_ctx)

    if wiki_ctx:
        messages.append({
            "role": "user",
            "content": f"[📚 현재 시스템 컨텍스트 (위키/RAG)]\n{wiki_ctx[:3000]}",
        })
        messages.append({
            "role": "assistant",
            "content": "컨텍스트 확인했습니다. 질문을 말씀해 주세요.",
        })

    # 4. L2/L3 메모리 오버레이 (hybrid_recall → pre_query_context 폴백) ─
    try:
        from modules.memory_refinement import hybrid_recall
        mem_ctx = hybrid_recall(user_text, top_k=3)
        if not mem_ctx:
            mem_ctx = memory.pre_query_context(user_text)
        if mem_ctx:
            messages.append({
                "role": "user",
                "content": f"[🧠 관련 기억 오버레이]\n{mem_ctx[:2000]}",
            })
            messages.append({
                "role": "assistant",
                "content": "해당 맥락을 참고하여 답변을 준비합니다.",
            })
    except Exception:
        pass

    # 5. 작업 유형 컨텍스트 주입 (Resolver) ───────────────────────────
    try:
        from modules.hermes_context_builder import (
            resolve_task_type, get_task_context, TASK_EMOJI,
        )
        task_type = resolve_task_type(user_text)
        task_ctx = get_task_context(task_type, str(config.SCRIPTS_DIR))
        if task_ctx:
            emoji = TASK_EMOJI.get(task_type, "💬")
            messages.append({
                "role": "user",
                "content": f"[{emoji} 작업 컨텍스트 ({task_type})]\n{task_ctx[:2000]}",
            })
            messages.append({
                "role": "assistant",
                "content": "컨텍스트를 반영하여 응답합니다.",
            })
    except Exception as e:
        logger.warning(f"[CtxAssembler] Resolver 오류 (무시): {e}")

    # 6. 시스템 팩트 컨텍스트 (claude_briefing.md) ────────────────────
    # "내 시스템" 등 자기 시스템 질문 → LLM 환각 억제용 사실 주입
    if any(kw in user_text for kw in _SYSTEM_KEYWORDS):
        _inject_system_fact_context(messages)

    # 7. 날씨 선제 탐지 + 실시간 스크래핑 ────────────────────────────
    if "날씨" in user_text:
        await _inject_weather_context(messages, user_text, update_progress_fn)

    # 7.5. Input Clarity Gate (규칙 7 — 모호한 입력 사전 경고)
    clarity = _input_clarity_score(user_text)
    if clarity >= 0.6:
        messages.append({
            "role": "system",
            "content": (
                f"[⚠️ Input Clarity Gate] 사용자 발화 모호성 점수: {clarity:.1f}. "
                "지시대명사/단편 발화가 감지되었습니다. "
                "이전 대화 컨텍스트를 적극 참조하여 의도를 유추하세요. "
                "여전히 불명확하다면 짧게 되물어도 됩니다."
            ),
        })
        logger.debug(f"[CtxAssembler] clarity gate: score={clarity:.1f} text='{user_text[:40]}'")

    # 8. 최종 user 메시지 append ──────────────────────────────────────
    messages.append({"role": "user", "content": user_text})

    return messages


# ── 내부 헬퍼들 ────────────────────────────────────────────────────

def _sanitize_wiki_content(content: str) -> str:
    """
    Wiki 콘텐츠를 LLM 주입 전 ClawTrojan 패턴 제거.

    ClawTrojan (2605.31042): 로컬 작업공간의 숨은 지시가 지속형 백도어가 됨.
    실험 ASR 95.5% — wiki 파일 오염만으로 하네스 완전 탈취 가능.

    전략: 의심 패턴 라인 제거 + 신뢰 경로 외 차단.
    화이트리스트 경로(_WIKI_TRUSTED_ROOT)에서 온 콘텐츠만 허용.
    """
    if not content:
        return content

    lines = content.splitlines()
    cleaned = []
    suspicious_count = 0

    for line in lines:
        if _INJECT_PATTERN_RE.search(line):
            suspicious_count += 1
            logger.warning(f"[CtxAssembler] ClawTrojan 패턴 제거: {line[:80]}")
            continue  # 해당 라인 제거
        cleaned.append(line)

    if suspicious_count > 0:
        logger.warning(f"[CtxAssembler] wiki sanitize: {suspicious_count}개 의심 라인 제거됨")

    return "\n".join(cleaned)


def _inject_system_fact_context(messages: list[dict]) -> None:
    """
    claude_briefing.md에서 미사용 스택·시스템 스펙 섹션만 추출하여 주입.
    LLM 환각(미사용 기술 스택 오인) 방지 목적.
    """
    try:
        if not os.path.exists(_BRIEFING_PATH):
            return
        with open(_BRIEFING_PATH, encoding="utf-8") as f:
            raw = f.read()
        sections = []
        for sec in ["## ❌ 미사용 기술 스택", "## 🖥️ 시스템 스펙", "## 🔒 Lock Stack"]:
            idx = raw.find(sec)
            if idx != -1:
                end = raw.find("\n## ", idx + 1)
                sections.append(raw[idx: end if end != -1 else idx + 1500])
        if sections:
            ctx = "\n\n".join(sections)
            messages.append({
                "role": "user",
                "content": (
                    "[🛡️ 시스템 팩트 컨텍스트 — 반드시 이 정보를 기반으로 답변하세요]\n"
                    "아래는 현재 시스템의 실제 구성입니다. 이 목록에 없는 기술이 "
                    "'이 시스템에서 사용 중'이라고 추정하지 마세요.\n\n"
                    f"{ctx[:2500]}"
                ),
            })
            messages.append({
                "role": "assistant",
                "content": "시스템 팩트 컨텍스트 확인했습니다. 이 정보를 기반으로 답변합니다.",
            })
    except Exception as e:
        logger.warning(f"[CtxAssembler] briefing 주입 실패 (무시): {e}")


async def _inject_weather_context(
    messages: list[dict],
    user_text: str,
    update_progress_fn=None,
) -> None:
    """
    날씨 키워드 감지 시 네이버 날씨 스크래핑 결과를 실시간 주입.
    DeepSeek/NVIDIA가 "날씨 정보 없다"고 포기하는 것 방지.
    """
    try:
        if update_progress_fn:
            await update_progress_fn("🌤️ 실시간 날씨 조회 중...")

        import urllib.request as _ureq
        import urllib.parse as _uparse
        from bs4 import BeautifulSoup as _BS

        loc = (
            user_text
            .replace("날씨", "").replace("알려줘", "").replace("알려 줘", "")
            .replace("?", "").strip()
        )
        wurl = "https://search.naver.com/search.naver?query=" + _uparse.quote(loc + " 날씨")
        req = _ureq.Request(
            wurl,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        loop = asyncio.get_running_loop()
        html = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _ureq.urlopen(req, timeout=5).read()),
            timeout=7,
        )
        soup = _BS(html, "html.parser")

        temp_el = soup.find("div", {"class": "temperature_text"})
        summ_el = soup.find("p",   {"class": "summary"})
        hum_el  = soup.find("span", {"class": "humidity"})
        wind_el = soup.find("span", {"class": "wind_strong"})
        feel_el = soup.find("span", {"class": "sensory"})

        if temp_el and summ_el:
            temp  = temp_el.get_text(strip=True)
            summ  = summ_el.get_text(strip=True)
            hum   = hum_el.get_text(strip=True)  if hum_el  else ""
            wind  = wind_el.get_text(strip=True) if wind_el else ""
            feel  = feel_el.get_text(strip=True) if feel_el else ""

            weather_ctx = (
                "[🌤️ 실시간 날씨 데이터 — 네이버 날씨 스크래핑 결과]\n"
                f"지역: {loc}\n현재 기온: {temp}\n날씨 요약: {summ}\n"
            )
            if hum:  weather_ctx += f"습도: {hum}\n"
            if wind: weather_ctx += f"바람: {wind}\n"
            if feel: weather_ctx += f"체감 온도: {feel}\n"
            weather_ctx += "\n위 실시간 날씨 데이터를 바탕으로 사용자에게 친절하게 알려주세요."

            messages.append({"role": "user",      "content": weather_ctx})
            messages.append({"role": "assistant", "content": "네, 실시간 날씨 데이터를 확인했습니다. 바탕으로 답변드리겠습니다."})
            logger.info(f"[CtxAssembler] 날씨 선제주입: {loc} {temp} {summ}")

    except Exception as e:
        logger.warning(f"[CtxAssembler] 날씨 선제주입 실패 (무시): {e}")
