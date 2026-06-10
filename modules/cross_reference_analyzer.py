"""
cross_reference_analyzer.py — 교차 참조 분석기 (v1.0)
==================================================
사용자 로컬 노트와 웹 논문 간 의미적 연결 분석.

PKM_2 설계 기준:
- TF-IDF 코사인 유사도 (기존 HybridKnowledgeIndexer 활용)
- 시간 감쇠 (오래된 노트 vs 최신 논문에 페널티)
- alignment_type: predict_and_realize / retrospective_match
- 임계값 0.7 이상만 인사이트로 포함

의존성:
- modules/knowledge_indexer.py (HybridKnowledgeIndexer._tokenize, _tfidf_vector, _cosine_sim)
"""

import math
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

log = logging.getLogger("cross_reference_analyzer")

# 지연 임포트
_indexer = None

def _get_indexer():
    global _indexer
    if _indexer is None:
        from modules.knowledge_indexer import HybridKnowledgeIndexer
        _indexer = HybridKnowledgeIndexer()
    return _indexer


def cross_reference(
    user_notes: List[Dict[str, Any]],
    web_papers: List[Dict[str, Any]],
    threshold: float = 0.7,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    각 노트-논문 쌍에 대해 TF-IDF 코사인 유사도 + 시간 감쇠 계산.

    Parameters:
        user_notes: 로컬 검색 결과 (각 항목: title, content, date/datetime 등)
        web_papers: 웹 검색 결과 (각 항목: title, abstract, published 등)
        threshold: 유사도 임계값 (기본 0.7)
        top_k: 상위 N개 인사이트만 반환

    Returns:
        [{
            "note_title": str,
            "paper_title": str,
            "confidence": float (유사도 * 시간감쇠),
            "similarity": float (순수 TF-IDF 유사도),
            "alignment_type": "predict_and_realize" | "retrospective_match",
        }, ...]
    """
    indexer = _get_indexer()
    insights: List[Dict[str, Any]] = []

    if not user_notes or not web_papers:
        log.debug("[CrossRef] 노트 또는 논문 없음")
        return []

    for note in user_notes:
        note_text = f"{note.get('title', '')} {note.get('content', '')}".strip()
        if not note_text:
            continue

        note_vec = indexer._tfidf_vector(note_text)
        if not note_vec:
            continue

        note_date = _parse_date_str(note.get("date") or note.get("datetime") or "")

        for paper in web_papers:
            paper_text = f"{paper.get('title', '')} {paper.get('abstract', '')}".strip()
            if not paper_text:
                continue

            paper_vec = indexer._tfidf_vector(paper_text)
            if not paper_vec:
                continue

            # 코사인 유사도
            sim = indexer._cosine_sim(note_vec, paper_vec)
            if sim == 0.0:
                continue

            # 시간 감쇠: 노트가 논문보다 오래되었을수록 페널티
            time_decay = 1.0
            paper_date = _parse_date_str(paper.get("published") or paper.get("publicationDate") or "")
            if note_date and paper_date and note_date < paper_date:
                days_diff = (paper_date - note_date).days
                if days_diff > 0:
                    time_decay = 0.9 ** (days_diff / 365)  # 연간 0.9

            confidence = sim * time_decay

            if confidence >= threshold:
                # alignment_type 결정
                if note_date and paper_date and note_date < paper_date:
                    align_type = "predict_and_realize"
                else:
                    align_type = "retrospective_match"

                insights.append({
                    "note_title": note.get("title", "내 노트")[:60],
                    "paper_title": paper.get("title", "웹 논문")[:80],
                    "confidence": round(confidence, 3),
                    "similarity": round(sim, 3),
                    "time_decay": round(time_decay, 3),
                    "alignment_type": align_type,
                })

    # confidence 내림차순 정렬
    insights.sort(key=lambda x: x["confidence"], reverse=True)
    return insights[:top_k]


def _parse_date_str(date_str: str) -> Optional[datetime]:
    """날짜 문자열 파싱 (여러 형식 지원)"""
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None
