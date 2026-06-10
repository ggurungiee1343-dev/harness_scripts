"""
timeline_builder.py — 통합 타임라인 빌더 (v1.0)
=============================================
웹 논문 + 로컬 노트 결과를 시간순으로 정렬하고 중복을 병합.

PKM_2 설계 기준:
- 날짜 파싱 (published / date / created)
- 시간순 정렬 (None은 뒤로)
- arXiv ID / DOI 기준 버전 병합 (최신 버전만 유지)
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
from itertools import groupby

# 지원하는 날짜 키 우선순위
_DATE_KEYS = ["published", "date", "created", "formal_date", "publicationDate"]


def _parse_date(item: Dict[str, Any]) -> Optional[datetime]:
    """여러 날짜 키 중 첫 번째 유효한 날짜 파싱"""
    for key in _DATE_KEYS:
        raw = item.get(key)
        if raw and isinstance(raw, str) and len(raw) >= 10:
            try:
                # ISO 형식 (2026-03-15)
                return datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                try:
                    # 20260315 형식
                    return datetime.strptime(raw[:8], "%Y%m%d")
                except ValueError:
                    continue
    return None


def merge_timeline(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    items: 각 항목은 dict (title, source, published/date 등 포함)
    반환: 날짜 정렬 + arXiv 중복 병합된 타임라인
    """
    if not items:
        return []

    # 각 항목에 파싱된 날짜 할당
    for item in items:
        item["_dt"] = _parse_date(item)

    # 날짜 기준 정렬 (오름차순, None은 뒤로)
    items.sort(key=lambda x: (x["_dt"] is None, x["_dt"] or datetime.min))

    # arXiv ID / DOI 기준 중복 병합 (같은 ID면 최신 버전만)
    # 로컬 노트 등 식별자가 없는 항목은 None 키로 그룹화되지 않도록
    # 고유 폴백 키 부여 (None이면 개별 항목으로 처리)
    merged = []
    items_sorted_by_id = sorted(items, key=lambda x: x.get("arxiv_id", "") or x.get("doi", "") or "")
    for key, group in groupby(
        items_sorted_by_id,
        key=lambda x: x.get("arxiv_id") or x.get("doi") or id(x),  # id(x)로 None 그룹핑 방지
    ):
        group_list = list(group)
        if key and len(group_list) > 1:
            # 같은 ID면 최신 날짜의 것만
            latest = max(group_list, key=lambda x: x["_dt"] or datetime.min)
            # 버전 정보 추가
            latest["version_note"] = f"중복 병합 ({len(group_list)}개 버전 중 최신)"
            merged.append(latest)
        else:
            merged.extend(group_list)

    # 다시 날짜 정렬
    merged.sort(key=lambda x: (x["_dt"] is None, x["_dt"] or datetime.min))

    # 임시 _dt 필드 제거
    for item in merged:
        item.pop("_dt", None)

    return merged
