"""
auto_topic_manager.py — 자동 주제 분류 및 클러스터링 (v1.0)
========================================================
새 논문/노트를 기존 주제 클러스터에 자동 할당.

PKM_2 설계 기준:
- TF-IDF + 코사인 유사도 기반 클러스터링
- 기존 주제 템플릿과의 유사도 비교
- 새 주제 발견 시 자동 등록 (매주 재클러스터링)

의존성:
- modules/knowledge_indexer.py (HybridKnowledgeIndexer)
"""

import json
import logging
import os
import math
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

log = logging.getLogger("auto_topic_manager")

# 주제 템플릿 저장 경로
TOPICS_DB_PATH = Path.home() / ".hermes" / "runtime" / "pkm_topics.json"
TOPIC_SIM_THRESHOLD = 0.35  # 이 값 이상이면 기존 주제에 할당

# 기본 주제 템플릿 (PKM 연구 맥락)
DEFAULT_TOPICS = [
    {
        "name": "AI/ML",
        "keywords": ["transformer", "neural network", "deep learning", "machine learning",
                      "attention", "llm", "large language model", "embedding",
                      "artificial intelligence", "reinforcement learning"],
        "created_at": "2026-01-01",
    },
    {
        "name": "법학/AI 규제",
        "keywords": ["algorithmic discrimination", "ai regulation", "평등권", "차별",
                      "법적 책임", "개인정보", "ai act", "규제", "저작권",
                      "책임", "입법"],
        "created_at": "2026-01-01",
    },
    {
        "name": "연구 방법론",
        "keywords": ["knowledge graph", "semantic search", "rag", "retrieval augmented",
                      "vector database", "knowledge mesh", "pipeline", "ontology",
                      "논문 검토", "메타 분석"],
        "created_at": "2026-01-01",
    },
    {
        "name": "자동화/시스템",
        "keywords": ["automation", "pipeline", "orchestrator", "workflow", "cron",
                      "watchdog", "agent", "monitoring", "load balancing",
                      "self healing", "cicd"],
        "created_at": "2026-01-01",
    },
    {
        "name": "기타/미분류",
        "keywords": [],
        "created_at": "2026-01-01",
    },
]


class AutoTopicManager:
    """
    TF-IDF 기반 자동 주제 분류기.
    새 문서의 TF-IDF 벡터와 기존 주제 템플릿 키워드 벡터 간
    코사인 유사도로 가장 가까운 주제 할당.
    """

    def __init__(self, topics_path: Optional[str] = None):
        self.topics_path = Path(topics_path) if topics_path else TOPICS_DB_PATH
        self._topics: List[Dict[str, Any]] = []
        self._load_topics()

    # ── 주제 저장/로드 ───────────────────────────────────────────────

    def _load_topics(self):
        """주제 템플릿 로드 (파일 없으면 기본값)"""
        if self.topics_path.exists():
            try:
                with open(self.topics_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._topics = data.get("topics", [])
                if self._topics:
                    log.info(f"[AutoTopic] 주제 템플릿 로드: {len(self._topics)}개")
                    return
            except Exception as e:
                log.warning(f"[AutoTopic] 주제 파일 로드 실패: {e}")
        self._topics = DEFAULT_TOPICS[:]
        self._save_topics()
        log.info(f"[AutoTopic] 기본 주제 템플릿 생성: {len(self._topics)}개")

    def _save_topics(self):
        """주제 템플릿 저장"""
        self.topics_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.topics_path, "w", encoding="utf-8") as f:
            json.dump({"topics": self._topics, "updated_at": datetime.now().isoformat()},
                      f, ensure_ascii=False, indent=2)

    # ── 주제 분류 ────────────────────────────────────────────────────

    def classify(self, title: str, abstract_or_content: str) -> Dict[str, Any]:
        """
        문서 제목 + 초록/내용 분석 → 가장 가까운 주제 반환.

        Returns:
            {"topic": str, "confidence": float, "is_new": bool}
        """
        indexer = self._get_indexer()
        doc_text = f"{title} {abstract_or_content}"
        doc_vec = indexer._tfidf_vector(doc_text)

        if not doc_vec:
            return {"topic": "기타/미분류", "confidence": 0.0, "is_new": False}

        best_topic = "기타/미분류"
        best_sim = 0.0

        for topic in self._topics:
            if not topic.get("keywords"):
                continue
            # 키워드 → TF-IDF 유사도
            kw_text = " ".join(topic["keywords"])
            kw_vec = indexer._tfidf_vector(kw_text)
            if not kw_vec:
                continue
            sim = indexer._cosine_sim(doc_vec, kw_vec)
            if sim > best_sim:
                best_sim = sim
                best_topic = topic["name"]

        return {
            "topic": best_topic if best_sim >= TOPIC_SIM_THRESHOLD else "기타/미분류",
            "confidence": round(best_sim, 3),
            "is_new": best_sim < TOPIC_SIM_THRESHOLD,
        }

    def add_topic(self, name: str, keywords: List[str]) -> bool:
        """새 주제 수동 등록"""
        if any(t["name"] == name for t in self._topics):
            return False
        self._topics.append({
            "name": name,
            "keywords": keywords,
            "created_at": datetime.now().isoformat()[:10],
        })
        self._save_topics()
        return True

    def suggest_new_topic(self, title: str, content: str) -> Optional[str]:
        """
        분류 불가 문서에서 새 주제 후보 제안.
        3개 이상의 유사 문서가 '기타/미분류'에 쌓이면 새 주제 제안.
        (현재는 간단히 None 반환 — 추후 확장)
        """
        return None

    def get_topics(self) -> List[Dict[str, Any]]:
        """현재 주제 목록"""
        return self._topics

    # ── 내부 헬퍼 ────────────────────────────────────────────────────

    _indexer_instance = None

    def _get_indexer(self):
        if AutoTopicManager._indexer_instance is None:
            from modules.knowledge_indexer import HybridKnowledgeIndexer
            AutoTopicManager._indexer_instance = HybridKnowledgeIndexer()
        return AutoTopicManager._indexer_instance


# 싱글톤
topic_manager = AutoTopicManager()
