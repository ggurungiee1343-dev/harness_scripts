"""
maritime_digest.py
매일 아침 해사법(maritime law) 뉴스·논문 요약을 Hermes1 텔레그램으로 발송.

배경 (2026-07-13, MJ님 요청): 법학 박사수료 + 현직 도선사(pilot) — 박사논문/학회
소논문 및 해양·도선·선박조종 분야 특허 아이디어 자료 확보 목적. 국적/언어 무관(2026-07-13 확정).
개수는 고정하지 않음(2026-07-14, MJ님: "양이 많은날은 많이보내고 적은날은 많은날꺼 좀빼서보내고")
— 관련도 임계값을 넘는 만큼만 자연스럽게, 하루 최대 12건(TOTAL_TARGET)까지.

[법령/고시] 카테고리 추가(2026-07-21, MJ님 요청): 항만 및 해안 기술사 취득 추진 중(project_harbor_engineer_license
참조) — 해양수산부(mof.go.kr)·법제처 국가법령정보센터(law.go.kr)의 항만·해안 관련 갱신·신규 법령/고시/훈령/예규를
찾아 분석. [뉴스]/[논문] 카테고리와 반대로 "법령 원문·기관 공지 자체"가 여기서는 채택 대상(뉴스 카테고리에서는
일부러 제외하는 것과 대조적).

기존 인프라 재사용:
  - 웹검색: duckduckgo_search(DDGS) — modules/core_agents.py의 기존 패턴과 동일
  - 요약: modules.llm_engines._call_nvidia (harness가 이미 쓰는 GPT OSS 120B 직접 호출)
  - 발송: send_telegram_msg.py와 동일한 방식(HERMES1_BOT_TOKEN, ~/.hermes/.env)

크론(06:30 매일, 06:30 추천 — MJ님 확정. 2026-07-21 등록 시 확인: 이 디렉토리엔 .venv가
없고 com.hermes.bot.plist와 동일하게 /usr/local/bin/python3 사용):
  30 6 * * * cd /Users/bluesea/Applications/Mjauto/Scripts && /usr/local/bin/python3 maritime_digest.py >> logs/maritime_digest.log 2>&1
"""

import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import requests
from dotenv import load_dotenv
from duckduckgo_search import DDGS

from modules.llm_engines import _call_nvidia

load_dotenv(str(Path.home() / ".hermes" / ".env"))

# 2026-07-22, MJ님 요청 — DDGS 키워드검색은 매일 결과가 불안정(오늘은 노이즈 위주)하므로,
# 신뢰도 높은 해사·항만 전문매체 RSS를 고정 소스로 병행 추가. MJ님이 지정한 5개 중 실제
# RSS 접근 가능 여부를 실측 확인(2026-07-22)한 결과만 채택:
#   - gCaptain(https://gcaptain.com/feed/): 정상 확인
#   - 해운산업신문 cargotimes.net(http://www.cargotimes.net/rss/allArticle.xml): 정상 확인
#   - 한국해운신문 maritimepress.co.kr(http://www.maritimepress.co.kr/rss/allArticle.xml): 정상 확인
#   - 해사신문 haesanews.com(http://www.haesanews.com/rss/allArticle.xml): 2026-07-22 재확인 —
#     https는 인증서 호스트명 불일치로 실패하지만 **http(비암호화)로는 200 정상 응답**(사이트
#     자체는 멀쩡, 인증서 발급만 잘못됨). RSS 단순 조회(GET, 자격정보 없음)라 http로도 무방해
#     채택함. 인증서가 정상화되면 https로 다시 바꿀 것.
# 미채택(추후 재검토 필요, 억지로 우회 구현하지 않음):
#   - Splash247: RSS 요청이 403(anti-bot 차단)으로 실패
#   - 코리아쉬핑가제트 ksg.co.kr: RSS 자체를 제공하지 않음(2026-07-22 홈페이지 <head> 확인 —
#     `<link rel="alternate" type="application/rss+xml">` 선언 없음, 뉴스스탠드·SNS로만 배포).
#     HTML 목록 페이지 직접 파싱은 사이트 구조 변경에 취약해 보류 — 필요시 별도 구현.
#   - IMO 보도자료 페이지: RSS 자체가 없음(정적 목록 페이지 스크레이핑이 필요해 별도 구현 과제로 남김)
#   - Lloyd's List, Journal of Maritime Law and Commerce: 유료/학술지 특성상 RSS 확인 안 됨
FIXED_RSS_SOURCES = [
    ("gCaptain", "https://gcaptain.com/feed/"),
    ("해운산업신문", "http://www.cargotimes.net/rss/allArticle.xml"),
    ("한국해운신문", "http://www.maritimepress.co.kr/rss/allArticle.xml"),
    ("해사신문", "http://www.haesanews.com/rss/allArticle.xml"),
]
FIXED_SOURCE_MAX_ITEMS = 5  # 소스당 최근 몇 건까지 관련도 평가 대상으로 가져올지


def _fetch_rss(name: str, url: str, max_items: int = FIXED_SOURCE_MAX_ITEMS) -> list[dict]:
    """RSS 2.0 피드에서 최신 항목을 (title, body, href) dict 리스트로 반환.
    실패해도 예외를 밖으로 던지지 않고 빈 리스트 반환 — 크론이 한 소스 장애로 죽지 않도록."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if title and link:
                items.append({"title": title, "body": desc, "href": link, "source": name})
        return items
    except Exception as e:
        print(f"[WARN] RSS 조회 실패 '{name}' ({url}): {e}")
        return []


NEWS_QUERIES = [
    "maritime law news IMO regulation 2026",
    "IMO Maritime Safety Committee new regulation site:imo.org",
    "ship maneuvering safety incident report",
    "pilotage accident OR near-miss harbor pilot",
    "maritime autonomous surface ship regulation news",
    # 2026-07-22, MJ님 요청 — "전세계 해양 뉴스인데 읽을거리가 없다": 위 5개가 해사법·도선·
    # 자율운항으로만 좁혀져 있어 그날 마침 소재가 없으면 통과 건수가 0에 가까웠음. 해운·항만
    # 산업 뉴스와 해양법·해양경계 분쟁 뉴스를 추가해 커버리지를 넓힘.
    # 쿼리 문구는 실측 검증 완료(2026-07-22) — 아래 4개는 실제 뉴스가 상위에 잡히는 것을 확인함.
    # 기각된 문구: "container shipping news..."(Docker/컨테이너 추적 서비스 노이즈),
    # "maritime casualty ship accident collision news"(무관한 중국어 여행 페이지 노이즈),
    # "maritime boundary dispute ITLOS UNCLOS ruling"(왓츠앱 노이즈),
    # "exclusive economic zone dispute news 2026"(영단어 사전 노이즈) — DDGS가 다의어를
    # bag-of-words로 처리해 흔한 단어(container, exclusive 등)가 무관한 페이지를 끌어옴.
    "global shipping industry news freight rates 2026",
    "cargo ship accident sinking news 2026",
    "port congestion shipping news 2026",
    "South China Sea maritime dispute news",
]
PAPER_QUERIES = [
    # 2026-07-14: MJ님 지시 — 국적/언어 무관, 해외 자료 동일 가중치. 법학 논문뿐 아니라
    # 도선·선박조종 관련 공학/기술 논문도 포함(특허 아이디어용, 해양·도선·선박조종 분야 특허 목표).
    "ship maneuvering research paper 2026 filetype:pdf",
    "pilotage decision support system research filetype:pdf",
    "maritime law review article 2026 filetype:pdf",
    "autonomous navigation collision avoidance research paper",
]
LAW_QUERIES = [
    # 2026-07-21: 항만 및 해안 기술사 취득 목적 — 해수부/법제처의 항만·해안 관련
    # 갱신·신규 법령·고시·훈령·예규. site: 연산자는 DDGS에서 한국 정부 도메인에 대해
    # 실측 결과 0건만 반환해(law.go.kr/mof.go.kr 전부 확인) 채택 안 함 — 일반 키워드
    # 검색이 실제로는 law.go.kr/mof.go.kr 원문을 상위 결과로 잘 잡아줌(실측 확인됨).
    "항만법 개정 법제처",
    "해안관리법 어촌어항법 개정 국가법령정보센터",
    "해양수산부 고시 항만시설 기술기준",
    "항만 및 어항 설계기준 개정",
    "해양수산부 훈령 예규 항만",
]

# 2026-07-14: "많은 날은 많이, 적은 날은 적게" — 고정 개수를 강제하지 않음.
# MAX_PER_QUERY: 한 쿼리에서 관련 있는 게 여러 건이면 그만큼 더 채택(뉴스거리 많은 날 대응).
# TOTAL_TARGET: 하한선이 아니라 상한선 — 텔레그램 메시지가 너무 길어지는 것만 방지.
MAX_PER_QUERY = 2
TOTAL_TARGET = 12

COMMON_INTRO = """당신은 해사법(maritime law) 전공 법학박사 수료생이자 현직 도선사(harbor pilot)인
전문가에게 브리핑하는 리서치 어시스턴트입니다. 목적은 두 가지입니다: ①박사논문/학회 소논문 아이디어,
②해양·도선(pilotage)·선박조종 분야 특허 아이디어.

**국적·언어는 전혀 신경 쓰지 마십시오** — 한국 자료든 해외(영어 등) 자료든 관련성만으로 동일하게
평가하십시오. 오히려 해외 최신 연구·기술·판례가 국내에 없는 새로운 논문·특허 아이디어의 원천일 수 있습니다.

**절대 금지 — 활용방안 날조(2026-07-22, MJ님 실측 지적)**: 실제로 두 건(중국 해양주권 논문집,
몽골 기국책임 논문)에서 원문에 "도선(pilotage)" 관련 내용이 전혀 없는데도 "(법학 연구 활용: 도선
의무·책임 비교분석)" 같은 문구가 붙어 발송된 사고가 있었다. 이는 브리핑 대상자가 도선사라는 배경
정보에 맞추기 위해 본문에도 없는 연결고리를 지어낸 것으로, 절대 반복되어서는 안 된다.
- SUMMARY의 "(법학 연구 또는 특허) 활용 방안" 줄은 아래 "본문 발췌"에 실제로 등장하는 개념·용어에
  근거해서만 작성한다. 도선·선박조종·특허라는 단어를 본문에서 찾을 수 없다면, 활용방안 줄에
  "도선"이나 "특허"를 언급하지 말고 실제 주제에 맞는 일반적 활용방안만 쓰거나, 마땅한 연결점이
  없으면 "직접적 활용점 없음 — 배경지식 참고용"이라고 정직하게 쓴다.
- 본문 발췌가 짧아(제목과 한두 문장 수준) 실제 내용을 판단하기 어려우면, 절대 구체적인 활용방안을
  지어내지 말고 "본문 발췌만으로는 구체 내용 확인 불가 — 원문 확인 필요"라고 명시한다.
- 검증되지 않은 추정을 확정된 사실처럼 서술하지 않는다.

**작성 후 반드시 스스로 재점검(기계적 규칙)**: SUMMARY를 다 쓴 뒤, 그 문장에 "도선"·"pilot"·
"pilotage"라는 단어가 하나라도 들어갔다면, 위에 주어진 "제목"과 "본문 발췌" 원문을 다시 한 글자씩
훑어서 그 단어(또는 명백한 동의어)가 실제로 있었는지 확인하라. 없다면 그 활용방안 문장은 즉시
삭제하고, 실제 본문 주제에 맞는 문장으로 고쳐 쓰거나 "직접적 활용점 없음"으로 대체하라. 이 재점검을
건너뛰고 "도선"을 습관적으로 넣는 것이 바로 위에서 지적한 실패 패턴이다."""

# 2026-07-14: MJ님 지적 — "뉴스보낸거봤는데 뉴스가아니고 날씨, 법소개 이런게섞여있네."
# 주제 관련성만 보고 채택하다 보니 법령 원문·기관 홈페이지 소개·날씨예보 도구 페이지 같은
# "참고자료"가 [뉴스] 칸에 섞여 들어간 문제. 뉴스와 논문의 판정 기준을 분리해서, 뉴스 카테고리는
# "실제 보도 기사인가"까지 별도로 요구하도록 함.
NEWS_SYS_PROMPT = COMMON_INTRO + """

아래 검색 결과가 **① 해사법·해상안전·도선·선박조종·선박안전규제·IMO·해양 자율운항 기술, 또는
해운·항만 산업 동향(운임·물동량·항만혼잡·조선·해운사 소식 등), 또는 해양법·해양경계 분쟁(남중국해,
EEZ·대륙붕 분쟁, ITLOS·UNCLOS 관련 판결·중재 등)과 실질적으로 관련 있고, 동시에 ② 실제 뉴스
기사(최근 사건·발표·사고·판결·정책변경 등을 보도하는 글)인지**(2026-07-22, MJ님 요청으로 해운
산업·해양경계 분쟁 범위 추가 — "전세계 해양 뉴스인데 읽을거리가 없다") 판단하십시오. 아래는 주제가
관련 있어 보여도 뉴스가 아니므로 **반드시 관련도 0~2점**으로 처리하십시오:
- 법령/조문 원문 자체(예: 국가법령정보센터의 법 조문 페이지)
- 기관·학회 홈페이지 소개/연혁 페이지
- 날씨·기상 예보 도구 페이지
- 백과사전(위키피디아 등) 개요 항목
- 선박 실시간 추적 서비스(MarineTraffic 등) 홈페이지 자체(단, 그 서비스를 다룬 보도기사라면 뉴스로 인정)
- 항만 혼잡·화물 지연 등을 실시간으로 보여주는 대시보드/트래킹 도구 페이지 자체(GoComet 등 — 특정
  사건·시점을 보도하는 기사가 아니라 상시 갱신되는 데이터 도구이므로, 그 도구를 소개하는 보도기사가
  아닌 한 관련도 0~2점으로 처리. 2026-07-22 실측: 이런 페이지가 뉴스로 오분류되며 본문에 없는
  "도선 의무" 활용방안까지 지어내는 사고가 있었음)

단순히 "선박"이라는 단어가 들어간 관광/커뮤니티/무관 페이지도 관련 없음으로 처리하십시오.

반드시 아래 형식으로만 답하십시오:
RELEVANCE: <0~10 정수>
SUMMARY: <뉴스로 인정되면 한국어 2~3문장 요약 + (법학 연구 또는 특허) 활용 방안 한 줄. 아니면 "관련 없음"만 기재>

과장하지 말고, 실제로 자료 내용에 근거해서만 작성하십시오."""

PAPER_SYS_PROMPT = COMMON_INTRO + """

아래 검색 결과가 논문·학술 리뷰·연구보고서·법령해설서 등 **학술/연구 자료**로서 해사법·해상안전·도선·
선박조종·선박안전규제·IMO·해양 자율운항 기술과 실질적으로 관련 있는지 판단하십시오(뉴스 기사 여부는
따지지 않습니다 — 오히려 논문·리포트·기술 리뷰가 이 카테고리의 정석입니다). 다만 아래는 학술자료가
아니므로 낮은 점수로 처리하십시오: 백과사전 개요 항목, 기관 홈페이지 소개 페이지, 단순 뉴스 기사.

반드시 아래 형식으로만 답하십시오:
RELEVANCE: <0~10 정수>
SUMMARY: <학술자료로 인정되면 한국어 2~3문장 요약 + (법학 연구 또는 특허) 활용 방안 한 줄. 아니면 "관련 없음"만 기재>

과장하지 말고, 실제로 자료 내용에 근거해서만 작성하십시오."""

# 2026-07-21: 항만 및 해안 기술사 취득 목적 — [뉴스]/[논문]과 반대로, 법령 조문 원문·
# 기관 고시/보도자료 자체가 채택 대상(오히려 그게 정석 자료). 기술사 실무·시험 범위와
# 무관한 일반 해양뉴스나 학술논문은 여기선 낮은 점수로 처리.
LAW_SYS_PROMPT = """당신은 항만 및 해안 기술사(harbor & coastal engineer) 자격을 준비 중인
현직 도선사에게 브리핑하는 리서치 어시스턴트입니다. 목적은 항만·해안 기술사 실무·시험에
도움되는 최신 법령·고시·훈령·예규 갱신 사항을 파악하는 것입니다.

아래 검색 결과가 **항만법·해안관리법·어촌어항법·항만 및 어항 설계기준·항만시설기준 등
항만/해안 공학·기술사 실무와 관련된 법령 조문, 해양수산부(또는 관련 지자체) 고시·훈령·예규,
또는 그런 개정·신설을 알리는 보도자료·공지인지** 판단하십시오. 다음은 채택 대상입니다(뉴스
카테고리와 반대로, 오히려 이게 정석 자료입니다):
- 법제처 국가법령정보센터의 법 조문 원문(항만·해안 관련 법령의 개정·신설 내용)
- 해양수산부(또는 지자체) 고시/훈령/예규 원문 또는 그 개정을 알리는 공지
- 항만·어항 설계기준, 방파제/안벽/호안 등 시설기준 관련 공식 문서

아래는 관련도 0~2점으로 처리하십시오:
- 항만/해안과 무관한 일반 해사법 뉴스(도선·선박조종·IMO 등 — 이건 [뉴스]/[논문] 카테고리 담당)
- 단순 관광/여객선 소식, 날씨, 백과사전 개요

반드시 아래 형식으로만 답하십시오:
RELEVANCE: <0~10 정수>
SUMMARY: <채택되면 한국어 2~3문장 요약 + 기술사 실무/시험 활용 방안 한 줄. 아니면 "관련 없음"만 기재>

과장하지 말고, 실제로 자료 내용에 근거해서만 작성하십시오."""

RELEVANCE_THRESHOLD = 6


async def _evaluate(title: str, body: str, url: str, content_type: str = "news") -> tuple[int, str]:
    """관련도 점수 + 요약을 함께 반환. (관련도, 요약문) — 관련도 낮으면 요약은 무시할 것.
    content_type: "news"면 실제 보도기사인지까지 요구, "paper"면 학술자료 기준,
    "law"면 항만·해안 기술사 관련 법령/고시 기준으로 판정."""
    sys_prompt = {"news": NEWS_SYS_PROMPT, "paper": PAPER_SYS_PROMPT, "law": LAW_SYS_PROMPT}[content_type]
    messages = [
        {"role": "system", "content": sys_prompt},
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


async def _find_relevant(query: str, want: int, seen_urls: set, pool_size: int = 5,
                          content_type: str = "news") -> list[tuple[dict, str]]:
    """검색 결과 pool 중 관련도 임계값을 넘는 상위 want개를 (raw_result, summary)로 반환.
    seen_urls에 있는 링크는 건너뛰고, 채택된 링크는 seen_urls에 즉시 추가(중복 방지)."""
    results = _search(query, max_results=pool_size)
    accepted = []
    for r in results:
        title, body, href = r.get("title", ""), r.get("body", ""), r.get("href", "")
        if not href or href in seen_urls:
            continue
        score, summary = await _evaluate(title, body, href, content_type=content_type)
        if score >= RELEVANCE_THRESHOLD:
            accepted.append((r, summary))
            seen_urls.add(href)
        if len(accepted) >= want:
            break
    return accepted


async def _find_relevant_from_items(items: list[dict], want: int, seen_urls: set,
                                     content_type: str = "news") -> list[tuple[dict, str]]:
    """이미 가져온 항목 리스트(RSS 등)를 대상으로 관련도 평가만 수행. _find_relevant와
    로직은 동일하되 검색(_search) 단계를 건너뛴다 — 고정 소스(RSS)용."""
    accepted = []
    for r in items:
        title, body, href = r.get("title", ""), r.get("body", ""), r.get("href", "")
        if not href or href in seen_urls:
            continue
        score, summary = await _evaluate(title, body, href, content_type=content_type)
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
    news_items, paper_items, law_items = [], [], []
    seen_urls: set[str] = set()

    def _total() -> int:
        return len(news_items) + len(paper_items) + len(law_items)

    # 2026-07-22: 고정 소스(RSS) 우선 처리 — DDGS 키워드검색보다 신뢰도가 높으므로 먼저 채택하고,
    # 이후 키워드검색은 그 빈자리(TOTAL_TARGET까지)를 채우는 보조 역할로 이어진다.
    for name, feed_url in FIXED_RSS_SOURCES:
        if _total() >= TOTAL_TARGET:
            break
        raw_items = _fetch_rss(name, feed_url)
        found = await _find_relevant_from_items(raw_items, want=MAX_PER_QUERY, seen_urls=seen_urls,
                                                 content_type="news")
        news_items.extend(found)

    for q in NEWS_QUERIES:
        if _total() >= TOTAL_TARGET:
            break
        found = await _find_relevant(q, want=MAX_PER_QUERY, seen_urls=seen_urls, pool_size=6,
                                      content_type="news")
        news_items.extend(found)

    for q in PAPER_QUERIES:
        if _total() >= TOTAL_TARGET:
            break
        found = await _find_relevant(q, want=MAX_PER_QUERY, seen_urls=seen_urls, pool_size=6,
                                      content_type="paper")
        paper_items.extend(found)

    for q in LAW_QUERIES:
        if _total() >= TOTAL_TARGET:
            break
        found = await _find_relevant(q, want=MAX_PER_QUERY, seen_urls=seen_urls, pool_size=6,
                                      content_type="law")
        law_items.extend(found)

    lines = ["⚓️ 해사법 아침 브리핑\n"]

    if news_items:
        lines.append("[뉴스]")
        for i, (r, summary) in enumerate(news_items, 1):
            title = r.get("title", "제목 없음")
            href = r.get("href", "")
            source_tag = f"[{r['source']}] " if r.get("source") else ""
            lines.append(f"{i}. {source_tag}{title}\n{summary}\n{href}\n")

    if paper_items:
        lines.append("[논문/학술자료]")
        for i, (r, summary) in enumerate(paper_items, 1):
            title = r.get("title", "제목 없음")
            href = r.get("href", "")
            lines.append(f"{i}. {title}\n{summary}\n{href}\n")

    if law_items:
        lines.append("[항만·해안기술사 관련 법령/고시]")
        for i, (r, summary) in enumerate(law_items, 1):
            title = r.get("title", "제목 없음")
            href = r.get("href", "")
            lines.append(f"{i}. {title}\n{summary}\n{href}\n")

    if not news_items and not paper_items and not law_items:
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
