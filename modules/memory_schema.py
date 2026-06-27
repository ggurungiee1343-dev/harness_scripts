"""
memory_schema.py — Hermes 메모리 데이터 구조 재설계

근거:
  TAOUP Rule of Representation:
    "Fold knowledge into data so program logic can be stupid and robust"
    → 지식을 데이터 구조에 접어넣으면 로직이 단순해짐

  Pike Rule 5:
    "Data structures, not algorithms, are central to programming"
    → 데이터 구조가 먼저, 알고리즘은 그 다음

  Exceeds AI — Delayed failure 방지:
    "Silent corruption that doesn't show up until much later"
    → L2가 조용히 쌓이다 30~90일 후 context 품질 저하
    → 저장 시점에 유효성 검사를 강제화

문제점 (현재):
  1. L2 episodic: 아무 기준 없이 쌓임 → 54항목 미처리 방치
  2. L3 semantic: patterns[] 배열에 무작위 누적 → 카테고리 없음
  3. consolidation: Dreaming 폐기 후 경로 없음 → 영원히 쌓임
  4. hybrid_recall: 3소스 전체 탐색 → 느림

해결:
  1. MemoryEntry 데이터클래스 — 저장 기준을 데이터에 내장
  2. L3를 카테고리 dict로 → 탐색 범위 축소
  3. 저장 시 자동 만료/중요도 검사 → delayed failure 방지
  4. recall 시 카테고리 필터 → 전체 탐색 불필요

설치 위치:
  ~/Applications/Mjauto/Scripts/modules/memory_schema.py
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Literal
import json, hashlib, time


# ──────────────────────────────────────────────────────────────────────────────
# 카테고리 정의 (Rule of Representation: 로직 대신 데이터로)
# ──────────────────────────────────────────────────────────────────────────────
CATEGORIES = Literal[
    "maritime_law",    # 해양법·논문
    "stock",           # 주식·MJstock
    "system",          # Hermes 하네스·설정
    "personal",        # 개인 컨텍스트·선호
    "research",        # 논문·연구
    "general",         # 분류 불가
]

# 카테고리 자동 분류 키워드 (로직 대신 데이터 테이블로)
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "maritime_law":  ["해양", "선박", "심판", "항만", "도선", "marine", "maritime", "해사", "선원"],
    "stock":         ["주식", "종목", "스캔", "삼돌이", "농사", "단타", "매수", "손절", "거래량",
                      "MJstock", "screener", "signal", "SMCI", "NVDA", "ticker"],
    "system":        ["hermes", "하네스", "메모리", "텔레그램", "스크립트", "모듈", "에이전트",
                      "config", "deepseek", "Claude", "llm", "cron"],
    "personal":      ["박사님", "가족", "딸", "울산", "선호", "스타일", "습관"],
    "research":      ["논문", "arxiv", "paper", "연구", "학술", "학회", "저널"],
}


def classify_category(text: str) -> str:
    """키워드 테이블로 카테고리 자동 분류. 로직 없이 데이터로 결정."""
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw.lower() in text_lower)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


# ──────────────────────────────────────────────────────────────────────────────
# L2 에피소딕 메모리 엔트리
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class EpisodicEntry:
    """
    L2 에피소딕 1개 항목.
    저장 기준이 데이터에 내장되어 있어 로직이 단순해짐.
    """
    content:    str
    category:   str                     = "general"
    importance: float                   = 3.0         # 1.0~5.0
    created_at: str                     = field(default_factory=lambda: datetime.now().isoformat())
    ttl_days:   int                     = 30          # 만료 기간
    source:     str                     = "conversation"  # conversation / ingest / tool
    uid:        str                     = field(default="")

    def __post_init__(self):
        if not self.uid:
            self.uid = hashlib.md5(
                f"{self.content[:50]}{self.created_at}".encode()
            ).hexdigest()[:8]
        if self.category == "general":
            self.category = classify_category(self.content)

    # ── 저장 가치 판단 (Rule of Representation: 판단 기준을 데이터에) ──────────
    @property
    def is_worth_storing(self) -> bool:
        """
        저장할 가치가 있는가?
        기준을 코드 로직이 아니라 데이터 속성으로 표현.
        """
        if len(self.content.strip()) < 30:      return False  # 너무 짧음
        if self.importance < 2.5:               return False  # 중요도 낮음
        if self._is_ephemeral():                return False  # 일시적 표현
        return True

    @property
    def is_expired(self) -> bool:
        created = datetime.fromisoformat(self.created_at)
        return datetime.now() > created + timedelta(days=self.ttl_days)

    @property
    def retention_score(self) -> float:
        """에빙하우스 보유율 근사. 낮을수록 삭제 우선순위"""
        created = datetime.fromisoformat(self.created_at)
        age_days = (datetime.now() - created).days
        import math
        return self.importance * math.exp(-age_days / (self.ttl_days * 0.5))

    def _is_ephemeral(self) -> bool:
        """일시적 표현 감지 (Rule of Representation: 패턴을 데이터로)"""
        EPHEMERAL_PATTERNS = [
            "오늘", "방금", "지금", "잠깐", "잠시", "나중에",
            "today", "just now", "later", "temporarily"
        ]
        return any(p in self.content for p in EPHEMERAL_PATTERNS)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EpisodicEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ──────────────────────────────────────────────────────────────────────────────
# L3 시맨틱 메모리 구조
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class SemanticPattern:
    """L3 패턴 1개. 카테고리가 있어 탐색 범위 축소 가능."""
    pattern:    str
    category:   str
    confidence: float  = 1.0
    created_at: str    = field(default_factory=lambda: datetime.now().isoformat())
    source_uid: str    = ""   # 원본 L2 uid (추적용)
    uid:        str    = field(default="")

    def __post_init__(self):
        if not self.uid:
            self.uid = hashlib.md5(
                f"{self.pattern[:50]}{self.category}".encode()
            ).hexdigest()[:8]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticPattern":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class SemanticMemory:
    """
    L3 시맨틱 메모리 저장소.
    기존: patterns[] 배열 무작위 누적
    개선: {category: [patterns]} dict → 카테고리 필터로 탐색 O(n/k)

    Rule of Representation:
      카테고리 구조를 데이터에 접어넣음 → recall 로직이 단순해짐
    """

    def __init__(self, path: str):
        self.path = path
        self._data: dict[str, list[dict]] = self._load()

    def _load(self) -> dict[str, list[dict]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 기존 포맷 마이그레이션 (patterns[] → {category: []})
            if isinstance(raw, dict) and "patterns" in raw:
                return self._migrate_legacy(raw)
            if isinstance(raw, dict) and not any(isinstance(v, list) for v in raw.values()):
                return {c: [] for c in CATEGORY_KEYWORDS.keys()} | {"general": []}
            return raw
        except (FileNotFoundError, json.JSONDecodeError):
            return {c: [] for c in list(CATEGORY_KEYWORDS.keys()) + ["general"]}

    def _migrate_legacy(self, raw: dict) -> dict:
        """
        기존 semantic_memory.json의 patterns[] 배열을
        카테고리별 dict로 마이그레이션.
        """
        new: dict[str, list] = {c: [] for c in list(CATEGORY_KEYWORDS.keys()) + ["general"]}
        for item in raw.get("patterns", []):
            text = item if isinstance(item, str) else str(item.get("pattern", item))
            cat  = classify_category(text)
            sp   = SemanticPattern(pattern=text, category=cat)
            new[cat].append(sp.to_dict())
        print(f"[migrate] {sum(len(v) for v in new.values())}개 패턴 마이그레이션 완료")
        return new

    def add(self, pattern: SemanticPattern) -> bool:
        """중복 제거 후 추가"""
        cat = pattern.category
        if cat not in self._data:
            self._data[cat] = []
        # 유사 패턴 중복 체크 (앞 50자 비교)
        existing = {d.get("pattern", "")[:50] for d in self._data[cat]}
        if pattern.pattern[:50] in existing:
            return False
        self._data[cat].append(pattern.to_dict())
        return True

    def recall(
        self,
        query: str,
        categories: list[str] | None = None,
        top_k: int = 5,
    ) -> list[SemanticPattern]:
        """
        카테고리 필터 탐색. categories=None이면 자동 감지.
        Rule of Representation: 로직이 단순 → 필터 후 키워드 매칭만
        """
        if categories is None:
            categories = [classify_category(query)]
            if categories[0] != "general":
                categories.append("general")  # general도 항상 포함

        results = []
        query_words = set(query.lower().split())
        for cat in categories:
            for d in self._data.get(cat, []):
                p = d.get("pattern", "")
                p_words = set(p.lower().split())
                overlap = len(query_words & p_words)
                if overlap > 0:
                    sp = SemanticPattern.from_dict(d)
                    results.append((overlap, sp))

        results.sort(key=lambda x: x[0], reverse=True)
        return [sp for _, sp in results[:top_k]]

    def stats(self) -> dict:
        return {cat: len(items) for cat, items in self._data.items()}

    def save(self) -> None:
        import os
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)  # atomic write (Rule of Robustness)

    def __len__(self) -> int:
        return sum(len(v) for v in self._data.values())
