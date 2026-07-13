"""
maritime_digest.py
매일 아침 해사법(maritime law) 뉴스·논문 요약을 Hermes1 텔레그램으로 발송.

배경 (2026-07-13, MJ님 요청): 법학 박사수료 + 현직 도선사(pilot) — 박사논문/학회
소논문 및 해양·도선·선박조종 분야 특허 아이디어 자료 확보 목적. 국적/언어 무관(2026-07-13 확정).
개수는 고정하지 않음(2026-07-14, MJ님: "양이 많은날은 많이보내고 적은날은 많은날꺼 좀빼서보내고")
— 관련도 임계값을 넘는 만큼만 자연스럽게, 하루 최대 12건(TOTAL_TARGET)까지.

기존 인프라 재사용:
  - 웹검색: duckduckgo_search(DDGS) — modules/core_agents.py의 기존 패턴과 동일
  - 요약: modules.llm_engines._call_nvidia (harness가 이미 쓰는 GPT OSS 120B 직접 호출)
  - 발송: send_telegram_msg.py와 동일한 방식(HERMES1_BOT_TOKEN, ~/.hermes/.env)

크론(06:30 매일, 06:30 추천 — MJ님 확정):
  30 6 * * * cd /Users/bluesea/Applications/Mjauto/Scripts && .venv/bin/python maritime_digest.py >> logs/maritime_digest.log 2>&1
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import requests
from dotenv import load_dotenv
from duckduckgo_search import DDGS

from modules.llm_engines import _call_nvidia

load_dotenv(str(Path.home() / ".hermes" / ".env"))

NEWS_QUERIES = [
    "maritime law news IMO regulation 2026",
    "IMO Maritime Safety Committee new regulation site:imo.org",
    "ship maneuvering safety incident report",
    "pilotage accident OR near-miss harbor pilot",
    "maritime autonomous surface ship regulation news",
]
PAPER_QUERIES = [
    # 2026-07-14: MJ님 지시 — 국적/언어 무관, 해외 자료 동일 가중치. 법학 논문뿐 아니라
    # 도선·선박조종 관련 공학/기술 논문도 포함(특허 아이디어용, 해양·도선·선박조종 분야 특허 목표).
    "ship maneuvering research paper 2026 filetype:pdf",
    "pilotage decision support system research filetype:pdf",
    "maritime law review article 2026 filetype:pdf",
    "autonomous navigation collision avoidance research paper",
]

# 2026-07-14: "많은 날은 많이, 적은 날은 적게" — 고정 개수를 강제하지 않음.
# MAX_PER_QUERY: 한 쿼리에서 관련 있는 게 여러 건이면 그만큼 더 채택(뉴스거리 많은 날 대응).
# TOTAL_TARGET: 하한선이 아니라 상한선 — 텔레그램 메시지가 너무 길어지는 것만 방지.
MAX_PER_QUERY = 2
TOTAL_TARGET = 12

SUMMARY_SYS_PROMPT = """당신은 해사법(maritime law) 전공 법학박사 수료생이자 현직 도선사(harbor pilot)인
전문가에게 브리핑하는 리서치 어시스턴트입니다. 목적은 두 가지입니다: ①박사논문/학회 소논문 아이디어,
②해양·도선(pilotage)·선박조종 분야 특허 아이디어. 아래 검색 결과 하나가 해사법·해상안전·도선·
선박조종·선박안전규제·IMO·해양 자율운항 기술 등과 실질적으로 관련이 있는지 먼저 판단하십시오
(단순히 "선박"이라는 단어가 들어간 관광/커뮤니티/무관 페이지는 관련 없음으로 처리).

**국적·언어는 전혀 신경 쓰지 마십시오** — 한국 자료든 해외(영어 등) 자료든 관련성만으로 동일하게
평가하십시오. 오히려 해외 최신 연구·기술·판례가 국내에 없는 새로운 논문·특허 아이디어의 원천일 수 있습니다.

반드시 아래 형식으로만 답하십시오:
RELEVANCE: <0~10 정수>
SUMMARY: <관련 있으면 한국어 2~3문장 요약 + (법학 연구 또는 특허) 활용 방안 한 줄. 관련 없으면 "관련 없음"만 기재>

과장하지 말고, 실제로 자료 내용에 근거해서만 작성하십시오."""

RELEVANCE_THRESHOLD = 6


async def _evaluate(title: str, body: str, url: str) -> tuple[int, str]:
    """관련도 점수 + 요약을 함께 반환. (관련도, 요약문) — 관련도 낮으면 요약은 무시할 것."""
    messages = [
        {"role": "system", "content": SUMMARY_SYS_PROMPT},
        {"role": "user", "content": f"제목: {title}\n본문 발췌: {body[:800]}\n출처: {url}"},
    ]
    try:
        content, _ = await _call_nvidia(messages)
        content = content.strip()
        score = 0
        summary = content
        for line in content.splitlines():
            if line.upper().startswith("RELEVANCE:"):
                try:
                    score = int("".join(ch for ch in line.split(":", 1)[1] if ch.isdigit()) or 0)
                except ValueError:
                    score = 0
            elif line.upper().startswith("SUMMARY:"):
                summary = line.split(":", 1)[1].strip()
        return score, summary
    except Exception as e:
        return 0, f"(요약 실패: {e})"


async def _find_relevant(query: str, want: int, seen_urls: set, pool_size: int = 5) -> list[tuple[dict, str]]:
    """검색 결과 pool 중 관련도 임계값을 넘는 상위 want개를 (raw_result, summary)로 반환.
    seen_urls에 있는 링크는 건너뛰고, 채택된 링크는 seen_urls에 즉시 추가(중복 방지)."""
    results = _search(query, max_results=pool_size)
    accepted = []
    for r in results:
        title, body, href = r.get("title", ""), r.get("body", ""), r.get("href", "")
        if not href or href in seen_urls:
            continue
        score, summary = await _evaluate(title, body, href)
        if score >= RELEVANCE_THRESHOLD:
            accepted.append((r, summary))
            seen_urls.add(href)
        if len(accepted) >= want:
            break
    return accepted


def _search(query: str, max_results: int = 3) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        print(f"[WARN] 검색 실패 '{query}': {e}")
        return []


async def build_digest() -> str:
    """건수를 억지로 6개에 맞추지 않는다(2026-07-14, MJ님: "양이 많은날은 많이보내고
    적은날은 많은날꺼 좀빼서보내고"). 쿼리마다 관련도 임계값을 넘는 결과가 있는 만큼만
    자연스럽게 채택 — 뉴스거리가 많은 날은 쿼리당 최대 MAX_PER_QUERY개까지 더 뽑고,
    없는 날은 그 쿼리에서 그냥 0개로 끝난다. TOTAL_TARGET은 상한선(과다 발송 방지)일 뿐
    목표치가 아니다."""
    news_items, paper_items = [], []
    seen_urls: set[str] = set()

    for q in NEWS_QUERIES:
        if len(news_items) + len(paper_items) >= TOTAL_TARGET:
            break
        found = await _find_relevant(q, want=MAX_PER_QUERY, seen_urls=seen_urls, pool_size=6)
        news_items.extend(found)

    for q in PAPER_QUERIES:
        if len(news_items) + len(paper_items) >= TOTAL_TARGET:
            break
        found = await _find_relevant(q, want=MAX_PER_QUERY, seen_urls=seen_urls, pool_size=6)
        paper_items.extend(found)

    lines = ["⚓️ 해사법 아침 브리핑\n"]

    if news_items:
        lines.append("[뉴스]")
        for i, (r, summary) in enumerate(news_items, 1):
            title = r.get("title", "제목 없음")
            href = r.get("href", "")
            lines.append(f"{i}. {title}\n{summary}\n{href}\n")

    if paper_items:
        lines.append("[논문/학술자료]")
        for i, (r, summary) in enumerate(paper_items, 1):
            title = r.get("title", "제목 없음")
            href = r.get("href", "")
            lines.append(f"{i}. {title}\n{summary}\n{href}\n")

    if not news_items and not paper_items:
        lines.append("오늘은 관련도 기준(RELEVANCE≥6)을 통과한 결과를 찾지 못했습니다 — 쿼리 재확인 필요.")

    return "\n".join(lines)


def send_telegram(text: str):
    bot_token = os.getenv("HERMES1_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    sys.path.insert(0, str(Path(__file__).parent))
    import config
    chat_id = config.ALLOWED_ID
    if not bot_token or not chat_id:
        print("[ERROR] 텔레그램 토큰/chat_id 없음")
        return
    # parse_mode 미지정(plain text) — 요약문에 마크다운 특수문자가 섞여 파싱 에러(400)
    # 나던 문제를 근본적으로 피함. 텔레그램 4096자 제한 대응 — 넘으면 분할 발송.
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for i in range(0, len(text), 3800):
        chunk = text[i:i + 3800]
        resp = requests.post(url, json={
            "chat_id": chat_id, "text": chunk,
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code != 200:
            print(f"[ERROR] 텔레그램 발송 실패: {resp.status_code} {resp.text[:200]}")


def main():
    digest = asyncio.run(build_digest())
    print(digest)
    send_telegram(digest)


if __name__ == "__main__":
    main()
