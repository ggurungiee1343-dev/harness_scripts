"""
Memory Refinement Engine v1.0 (Phase 2)
bio_memory_engine.py(Lock Stack)를 건드리지 않고 4대 갭을 해결하는 래퍼 모듈.

해결 갭:
  1. Forget 정책 부재 → auto_forget()
  2. Update 충돌 감지 → check_conflict()
  3. Writer self-question → should_store()
  4. Retrieval 품질 → hybrid_recall()
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("HermesOrchestrator")

# ── 경로 상수 ────────────────────────────────────────────────────
_BIO_DIR = Path("/Users/bluesea/.hermes/memory")
_L2_PATH = _BIO_DIR / "episodic_memory.json"
_L3_PATH = _BIO_DIR / "semantic_memory.json"
_SEMANTIC_DB = Path("/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta/semantic_index.db")

# ── 설정 상수 ────────────────────────────────────────────────────
FORGET_RETENTION_THRESHOLD = 0.15   # 보유율 15% 미만이면 forget 대상
FORGET_MIN_AGE_DAYS = 7             # 최소 7일 경과한 에피소드만 대상
STORE_IMPORTANCE_FLOOR = 3.0        # 중요도 3.0 미만이면 저장 보류
STORE_DECAY_DAYS = 7                # "7일 후에도 쓸모있나?" 기준
CONFLICT_KEYWORD_OVERLAP = 2        # 키워드 2개 이상 겹치면 충돌 후보


# ── JSON 유틸 ────────────────────────────────────────────────────
def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic write


# ══════════════════════════════════════════════════════════════════
# 1. Forget 정책 — auto_forget()
# ══════════════════════════════════════════════════════════════════

def auto_forget(dry_run: bool = True) -> Dict:
    """
    L2 에피소드 중 보유율 < FORGET_RETENTION_THRESHOLD &
    최소 FORGET_MIN_AGE_DAYS 경과한 항목을 정리.

    Args:
        dry_run: True면 삭제하지 않고 대상 목록만 반환

    Returns:
        {"candidates": [...], "removed": int, "remaining": int}
    """
    import math

    l2 = _load_json(_L2_PATH)
    episodes = l2.get("episodes", [])
    associations = l2.get("associations", {})
    now = datetime.now(timezone.utc)

    candidates = []
    keep = []

    for ep in episodes:
        importance = ep.get("importance", 1.0)
        last_accessed = ep.get("last_accessed", ep.get("timestamp", ""))

        # 보유율 계산 (에빙하우스)
        try:
            last = datetime.fromisoformat(last_accessed)
        except Exception:
            last = now
        days_elapsed = (now - last).total_seconds() / 86400
        stability = importance * 5.0
        retention = round(math.exp(-days_elapsed / max(stability, 0.1)), 4)

        if retention < FORGET_RETENTION_THRESHOLD and days_elapsed >= FORGET_MIN_AGE_DAYS:
            candidates.append({
                "id": ep.get("id", "?"),
                "content": ep.get("content", "")[:80],
                "importance": importance,
                "retention": retention,
                "days_old": round(days_elapsed, 1),
            })
        else:
            keep.append(ep)

    removed = 0
    if not dry_run and candidates:
        # 연관 엣지도 정리
        removed_ids = {c["id"] for c in candidates}
        cleaned_assoc = {}
        for ep_id, edges in associations.items():
            if ep_id in removed_ids:
                continue
            cleaned_assoc[ep_id] = [e for e in edges if e.get("target") not in removed_ids]

        l2["episodes"] = keep
        l2["associations"] = cleaned_assoc
        _save_json(_L2_PATH, l2)
        removed = len(candidates)
        logger.info(f"[MemRefine] 🧹 L2 forget 완료: {removed}개 제거, {len(keep)}개 유지")

    return {
        "candidates": candidates,
        "removed": removed,
        "remaining": len(keep),
        "total_before": len(episodes),
    }


# ══════════════════════════════════════════════════════════════════
# 2. Update 충돌 감지 — check_conflict()
# ══════════════════════════════════════════════════════════════════

def check_conflict(key: str, new_value: str) -> List[Dict]:
    """
    save_important() 전에 기존 L2/L3에 충돌하는 기억이 있는지 검사.

    Args:
        key: 저장하려는 키
        new_value: 저장하려는 값

    Returns:
        충돌 후보 리스트 [{"source": "L2"|"L3", "content": ..., "similarity": ...}]
    """
    from modules.bio_memory_engine import ImportanceScorer

    new_kws = set(ImportanceScorer.extract_keywords(f"{key} {new_value}", max_kw=8))
    conflicts = []

    # L2 에피소드 검색
    l2 = _load_json(_L2_PATH)
    for ep in l2.get("episodes", []):
        ep_kws = set(ep.get("keywords", []))
        overlap = len(new_kws & ep_kws)
        if overlap >= CONFLICT_KEYWORD_OVERLAP:
            conflicts.append({
                "source": "L2",
                "id": ep.get("id", "?"),
                "content": ep.get("content", "")[:150],
                "overlap_keywords": list(new_kws & ep_kws),
                "overlap_count": overlap,
            })

    # L3 패턴 검색
    l3 = _load_json(_L3_PATH)
    for pat in l3.get("patterns", []):
        pat_kws = set(pat.get("keywords", []))
        overlap = len(new_kws & pat_kws)
        if overlap >= CONFLICT_KEYWORD_OVERLAP:
            conflicts.append({
                "source": "L3",
                "content": pat.get("summary", "")[:150],
                "overlap_keywords": list(new_kws & pat_kws),
                "overlap_count": overlap,
            })

    # 유사도 내림차순 정렬
    conflicts.sort(key=lambda c: c["overlap_count"], reverse=True)
    return conflicts[:5]


# ══════════════════════════════════════════════════════════════════
# 3. Writer self-question — should_store()
# ══════════════════════════════════════════════════════════════════

def should_store(text: str, role: str = "user") -> Tuple[bool, str]:
    """
    "7일 후에도 쓸모있을까?" 자동 판단.

    Returns:
        (store: bool, reason: str)
    """
    from modules.bio_memory_engine import ImportanceScorer

    score = ImportanceScorer.score(text, role)

    # 중요도가 바닥이면 저장 불필요
    if score < STORE_IMPORTANCE_FLOOR:
        return False, f"중요도 {score:.1f} < {STORE_IMPORTANCE_FLOOR} (저장 기준 미달)"

    # 너무 짧은 내용
    if len(text.strip()) < 20:
        return False, "내용이 너무 짧음 (20자 미만)"

    # 일시적 표현 감지 (오늘/지금/방금 등)
    ephemeral_markers = ["지금", "방금", "아까", "잠깐", "임시", "테스트", "ㅋㅋ", "ㅎㅎ", "ㅇㅋ"]
    ephemeral_count = sum(1 for m in ephemeral_markers if m in text)
    if ephemeral_count >= 2:
        return False, f"일시적 표현 {ephemeral_count}개 감지 — 7일 후 무가치 예측"

    # 기존 L2에 유사 에피소드가 이미 3개 이상이면 중복
    from modules.bio_memory_engine import ImportanceScorer as IS
    new_kws = set(IS.extract_keywords(text, max_kw=5))
    l2 = _load_json(_L2_PATH)
    similar_count = 0
    for ep in l2.get("episodes", []):
        ep_kws = set(ep.get("keywords", []))
        if len(new_kws & ep_kws) >= 3:
            similar_count += 1
    if similar_count >= 3:
        return False, f"유사 에피소드 {similar_count}개 이미 존재 — 중복 저장 방지"

    return True, f"저장 권장 (중요도 {score:.1f}, 유사 {similar_count}개)"


# ══════════════════════════════════════════════════════════════════
# 4. Retrieval 품질 — hybrid_recall()
# ══════════════════════════════════════════════════════════════════

def hybrid_recall(query: str, top_k: int = 5) -> str:
    """
    bio_memory.recall() + knowledge_indexer FTS5 결과를 RRF 병합.

    bio_memory의 pre_query_context()가 L2/L3만 검색하는 것을 보완하여
    semantic_index.db(위키 전문검색)까지 포함한 통합 결과 반환.

    Returns:
        LLM 주입용 텍스트 (없으면 빈 문자열)
    """
    parts = []

    # 1. Bio-Memory L2/L3 recall (기존 엔진 활용)
    try:
        from modules.bio_memory_engine import BioMemoryEngine
        bio = BioMemoryEngine()
        l2_results = bio.recall(query, top_k=top_k)
        if l2_results:
            parts.append("🧠 [L2 연상 기억]")
            for ep in l2_results:
                ts = ep.get("timestamp", "")[:16].replace("T", " ")
                imp = ep.get("importance", 0)
                parts.append(f"  · [{ts}] ⭐{imp:.1f} — {ep.get('content', '')[:200]}")
    except Exception as e:
        logger.warning(f"[MemRefine] L2 recall 실패: {e}")

    # 2. Knowledge Indexer FTS5+TF-IDF (위키 전문검색)
    try:
        from modules.knowledge_indexer import HybridKnowledgeIndexer
        indexer = HybridKnowledgeIndexer()
        results = indexer.search_similar(query, top_k=top_k)
        if results:
            parts.append("📚 [위키 전문검색]")
            for r in results[:3]:
                doc = r.get("doc_path", "").split("/")[-1]
                snippet = r.get("content", "")[:200]
                parts.append(f"  · [{doc}] {snippet}")
    except Exception as e:
        logger.warning(f"[MemRefine] FTS5 검색 실패: {e}")

    if not parts:
        return ""

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════
# 통합: get_memory_health()
# ══════════════════════════════════════════════════════════════════

def get_memory_health() -> str:
    """
    메모리 건강 상태 요약 (forget 대상 수, L2 포화도 등).
    /memory 서브커맨드에서 활용.
    """
    import math

    l2 = _load_json(_L2_PATH)
    episodes = l2.get("episodes", [])
    now = datetime.now(timezone.utc)

    forget_count = 0
    low_importance = 0
    total_retention = 0.0

    for ep in episodes:
        importance = ep.get("importance", 1.0)
        last_accessed = ep.get("last_accessed", ep.get("timestamp", ""))
        try:
            last = datetime.fromisoformat(last_accessed)
        except Exception:
            last = now
        days = (now - last).total_seconds() / 86400
        stability = importance * 5.0
        retention = math.exp(-days / max(stability, 0.1))
        total_retention += retention

        if retention < FORGET_RETENTION_THRESHOLD and days >= FORGET_MIN_AGE_DAYS:
            forget_count += 1
        if importance < STORE_IMPORTANCE_FLOOR:
            low_importance += 1

    avg_retention = (total_retention / len(episodes) * 100) if episodes else 0

    return (
        f"🔬 <b>메모리 정제 상태</b>\n\n"
        f"• L2 에피소드: {len(episodes)}개\n"
        f"• 평균 보유율: {avg_retention:.0f}%\n"
        f"• 🧹 Forget 대상: {forget_count}개 (보유율 {FORGET_RETENTION_THRESHOLD*100:.0f}% 미만 + 7일 경과)\n"
        f"• ⚠️ 저중요도: {low_importance}개 (중요도 {STORE_IMPORTANCE_FLOOR:.0f} 미만)\n\n"
        f"💡 <code>/memory forget</code> — 대상 확인 (dry-run)\n"
        f"💡 <code>/memory forget confirm</code> — 실제 정리 실행"
    )
