"""
natural_language_router.py — 자연어 의도 분류기 v1.0
=================================================
규칙 기반 키워드 매칭으로 사용자 메시지 의도를 분류합니다.
LLM 호출 없이 동작하며, handle_text_message()의 최상단에서 호출됩니다.

사용:
    from natural_language_router import parse
    result = parse("파일 좀 읽어줘")
    # => {"intent": "file_command", "confidence": 0.85, "entities": {}}
"""

import re
import logging

logger = logging.getLogger('HermesOrchestrator')

# ── 의도별 키워드 패턴 (개별 키워드 기반, 유연한 매칭) ──────────
# 각 키워드는 텍스트 내 어디든 등장 가능
_INTENT_PATTERNS = {
    "file_command": [
        # 파일 읽기/쓰기/생성/삭제/이동/복사/이름변경
        "파일",
        "문서",
        "읽어",
        "읽기",
        "만들",
        "생성",
        "삭제",
        "지워",
        "이동",
        "옮겨",
        "복사",
        "이름",
        "변경",
        "내용물",
        "폴더",
        "디렉토리",
        "목록",
        "리스트",
        "저장",
        "기록",
        "쓰기",
        "작성",
        "열",
        "펼",
        "보여",
        "read",
        "delete",
        "remove",
        "copy",
        "move",
        "rename",
        "open",
        "create",
        "show",
        "edit",
        "update",
        "path",
    ],
    "search": [
        # 웹/내부 검색
        "검색",
        "찾아",
        "웹",
        "인터넷",
        "구글",
        "google",
        "뉴스",
        "트렌드",
        "알려줘",
        "소식",
        "온라인",
        "정보를",
        "자료",
    ],
    "ask_question": [
        # 질문/설명 요청
        "설명",
        "뜻",
        "의미",
        "질문",
        "궁금",
        "어떻",
        "왜",
        "언제",
        "어디",
        "누가",
        "무엇",
        "뭐",
        "알려",
        "말해",
        "가르쳐",
        "이해",
        "차이",
        "비교",
        "정의",
        "뭐야",
        "뭔지",
    ],
    "system_status": [
        # 상태 확인/모니터링
        "상태",
        "점검",
        "CPU",
        "메모리",
        "memory",
        "디스크",
        "disk",
        "GPU",
        "ram",
        "시스템",
        "건강",
        "돌아",
        "작동",
        "모니터링",
        "모니터",
        "부하",
        "성능",
        "performance",
        "호스트",
        "서버",
        "얼마나",
        "체크",
    ],
    "general_chat": [
        # 일반 대화 (fallback)
        "안녕",
        "하이",
        "hello",
        "hi",
        "반가",
        "고마",
        "thanks",
        "감사",
        "thx",
        "그래",
        "응",
        "네",
        "아니",
        "농담",
        "joke",
        "웃기",
        "재미",
        "생각",
        "의견",
        "opinion",
        "오늘",
        "날씨",
        "기분",
        "그냥",
    ],
}


# ── 엔티티 추출 패턴 ──────────────────────────────────────
_ENTITY_PATTERNS = {
    "filename": [
        # 한글: "파일명.ext 파일 읽어줘" / "파일명.ext 읽어줘"
        r"([\w가-힣\-.\\/]{3,}\.\w+)\s*(?:파일|문서|읽|열|보|삭제|이동|복사|만들)",
        # 영문: "read file.py" / "open file.py" / "delete file.py"
        r"(?:read|open|show|delete|copy|move)\s+([\w가-힣\-. \\/]{3,}\.\w+)",
        # 경로 패턴: handlers/_base.py, /path/to/file.txt
        r"([\w가-힣\-./]{4,}\.\w+)",
    ],
    "search_term": [
        r"(?:검색|찾|search)\s*(?:아|을게|을까)?\s*(.+?)(?:[줘]|[.。！!?？]|$)",
        r"(?:에 대해|about|regarding)\s*(.+?)(?:[.。！!?？]|$)",
        r"([\w\s가-힣]{2,})에\s*(?:관|대해|알|검색)",
    ],
    "question": [
        r"(?:무엇|뭐|who|what|when|where|why|how)\s*(?:이|가|은|는)?\s*(.+?)(?:[?？?]|$)",
        r"(?:설명|알려|말해|가르쳐)\s*(?:줘|주)[:：\s]+(.+?)(?:[.。！!?？]|$)",
        r"(.+?)(?:이\s*(?:뭐|무엇|왜|어떻)|(?:차이|뜻|의미))\s*(?:가|는)?\s*(?:뭐|어떻|무엇)",
    ],
}


def parse(text: str) -> dict:
    """
    주어진 텍스트의 의도를 분류합니다.

    Args:
        text: 사용자 메시지 (strip된 문자열)

    Returns:
        dict: {
            "intent": str — file_command / search / ask_question / general_chat / system_status
            "confidence": float — 0.0 ~ 1.0
            "entities": dict — 추출된 엔티티 (filename, search_term, question 등)
        }
    """
    if not text:
        return {"intent": "general_chat", "confidence": 0.0, "entities": {}}

    text_lower = text.lower().strip()
    original_text = text  # 엔티티 추출용 원본 보존

    # ── 의도 점수 계산 ──
    scores = {}
    for intent, keywords in _INTENT_PATTERNS.items():
        matches = sum(1 for kw in keywords if kw.lower() in text_lower)
        if matches > 0:
            # 점수 = 매칭된 키워드 수 / 전체 키워드 수
            scores[intent] = matches / len(keywords)

    # ── 최고 점수 의도 선택 ──
    if scores:
        best_intent = max(scores, key=scores.get)
        raw_score = scores[best_intent]

        # 신뢰도 계산: 키워드 밀도 보정
        total_words = max(len(text_lower.split()), 1)
        keyword_match_count = sum(
            1 for kw in _INTENT_PATTERNS[best_intent] if kw.lower() in text_lower
        )
        keyword_density = keyword_match_count / total_words
        confidence = min(raw_score * 10 + keyword_density * 0.5, 1.0)

        # 보정: 1개 키워드라도 강한 의도면 최소 confidence 보장
        if best_intent in ("file_command", "system_status") and confidence < 0.3:
            confidence = 0.3
        if best_intent == "ask_question" and confidence < 0.4:
            confidence = 0.4
    else:
        # 매칭 실패 → 문장 길이로 판별
        word_count = len(text_lower.split())
        if word_count <= 3:
            best_intent = "general_chat"
            confidence = 0.3
        else:
            if "?" in text or "？" in text:
                best_intent = "ask_question"
                confidence = 0.5
            else:
                best_intent = "general_chat"
                confidence = 0.2

    # ── 엔티티 추출 ──
    entities = {}
    for entity_type, patterns in _ENTITY_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, original_text)
            if match:
                value = match.group(1).strip()
                if value and len(value) < 200:
                    entities[entity_type] = value
                    break

    return {
        "intent": best_intent,
        "confidence": round(confidence, 2),
        "entities": entities,
    }


def classify_for_harness(intent_info: dict) -> str:
    """
    harness_agent의 handle_message에 전달할 추가 힌트 문자열 생성.

    Args:
        intent_info: parse()의 반환값

    Returns:
        str: "intent=file_command confidence=0.85" 형태의 힌트
    """
    return f"intent={intent_info['intent']} confidence={intent_info['confidence']}"


if __name__ == "__main__":
    # 간단한 테스트
    test_cases = [
        ("파일 좀 읽어줘", "file_command"),
        ("웹에서 검색해줘", "search"),
        ("이게 뭐야?", "ask_question"),
        ("안녕하세요", "general_chat"),
        ("시스템 상태 어때?", "system_status"),
        ("새로운 파일을 만들고 싶어", "file_command"),
        ("구글에서 파이썬 튜토리얼 검색해줘", "search"),
        ("딥러닝에 대해 설명해줘", "ask_question"),
        ("CPU 사용률 좀 봐줘", "system_status"),
        ("그냥 하는 말이야", "general_chat"),
    ]
    print("=== 의도 분류기 테스트 ===\n")
    for text, expected in test_cases:
        result = parse(text)
        status = "✓" if result["intent"] == expected else "✗"
        print(
            f"  {status} [{result['intent']:15s}] "
            f"(conf={result['confidence']:.2f}) "
            f"← '{text[:30]}'"
        )
