"""
Memory Refinement Engine v2.0 (Phase 3)
bio_memory_engine.py 위에서 동작하는 메모리 품질 관리 레이어.

해결 갭:
  1. Forget 정책 부재 → auto_forget()
  2. Update 충돌 감지 → check_conflict()
  3. Writer self-question → should_store()
  4. Retrieval 품질 → hybrid_recall()
  5. [NEW] SAGE Novelty Gate → novelty_score() / is_novel_enough()
  6. [NEW] Model Collapse 다양성 모니터 → diversity_check()

참조 논문:
  - SAGE (2605.30711): 메모리 쓰기 전 novelty gate로 품질·비용 최적화
  - Bi-Temporal Memory Engine (2606.09900): 컨텍스트 압축 + 선택적 보존
  - Model Collapse (Oxford 2305.17493): LLM 자기 참조 피드백 루프 방어
  - Observability-Safe Memory Retention (2606.10616): 접근 빈도 기반 보존 정책
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

# ── SAGE Novelty Gate 설정 (2605.30711) ──────────────────────────────
NOVELTY_SIMILARITY_THRESHOLD = 0.85  # 기존 에피소드와 이 이상 유사하면 중복으로 간주
NOVELTY_KEYWORD_OVERLAP_MAX = 4      # 키워드 4개 이상 겹치면 중복 후보
NOVELTY_FORCE_WRITE_DIFF_THRESHOLD = 0.3  # 유사하지만 결과가 다르면 강제 저장 (MMPO 2605.30159)

# ── Model Collapse 다양성 모니터 설정 (Oxford 2305.17493) ───────────────
DIVERSITY_MIN_BIGRAM_RATIO = 0.3    # 고유 bigram 비율이 이 미만이면 다양성 저하 경고
DIVERSITY_SAMPLE_SIZE = 50          # 최근 N개 에피소드로 다양성 계산
DIVERSITY_HUMAN_MIN_RATIO = 0.30    # human 출처 에피소드 최소 비율 (30% 미만이면 경고)


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
# 5. SAGE Novelty Gate — novelty_score() / is_novel_enough()
# ══════════════════════════════════════════════════════════════════

def novelty_score(text: str, role: str = "user") -> float:
    """
    새 텍스트가 기존 L2 에피소드 대비 얼마나 새로운지 점수 반환 (0.0~1.0).

    1.0 = 완전히 새로운 내용, 0.0 = 기존과 완전 동일.

    SAGE (2605.30711): novelty gate로 메모리 쓰기 품질·비용 최적화.
    Bi-Temporal (2606.09900): "메모리 = 한뜸" — 진짜 새로운 것만 저장.
    """
    from modules.bio_memory_engine import ImportanceScorer
    l2 = _load_json(_L2_PATH)
    episodes = l2.get("episodes", [])
    if not episodes:
        return 1.0  # 에피소드 없으면 무조건 새로움

    new_kws = set(ImportanceScorer.extract_keywords(text, max_kw=8))
    if not new_kws:
        return 0.5  # 키워드 없으면 중립

    # 같은 source(role) 에피소드와만 비교 — human/llm 교차 비교 방지
    same_source = [ep for ep in episodes if ep.get("source", "human") == ("human" if role == "user" else "llm")]
    compare_pool = same_source[-30:] if same_source else episodes[-30:]  # 최근 30개

    max_overlap_ratio = 0.0
    for ep in compare_pool:
        ep_kws = set(ep.get("keywords", []))
        if not ep_kws:
            continue
        overlap = len(new_kws & ep_kws)
        ratio = overlap / max(len(new_kws), len(ep_kws))
        max_overlap_ratio = max(max_overlap_ratio, ratio)

    return round(1.0 - max_overlap_ratio, 3)


def is_novel_enough(text: str, role: str = "user", force_if_different_outcome: bool = False) -> tuple:
    """
    SAGE Novelty Gate: 저장할 만큼 새로운지 판단.

    Args:
        text: 저장 후보 텍스트
        role: "user" | "assistant"
        force_if_different_outcome: True면 유사해도 결과가 다르면 강제 저장 (MMPO 원칙)

    Returns:
        (is_novel: bool, score: float, reason: str)
    """
    score = novelty_score(text, role)

    if score >= (1.0 - NOVELTY_SIMILARITY_THRESHOLD):
        return True, score, f"신규 콘텐츠 (novelty {score:.2f})"

    # 유사하지만 결과가 다른 경우 강제 저장 (MMPO 2605.30159: belief clarity)
    if force_if_different_outcome and score >= NOVELTY_FORCE_WRITE_DIFF_THRESHOLD:
        return True, score, f"유사하나 결과 상이 → 강제 저장 (MMPO원칙, novelty {score:.2f})"

    return False, score, f"중복 감지 — novelty {score:.2f} < {1.0 - NOVELTY_SIMILARITY_THRESHOLD:.2f}"


# ══════════════════════════════════════════════════════════════════
# 6. Model Collapse 다양성 모니터 — diversity_check()
# ══════════════════════════════════════════════════════════════════

def diversity_check() -> Dict:
    """
    최근 N개 에피소드의 응답 다양성 지수 측정.

    Model Collapse (Oxford 2305.17493): 피드백 루프로 꼬리 분포 소멸 감지.
    - bigram 다양성 비율 (unique bigrams / total bigrams)
    - human/llm 출처 비율
    - 반복 패턴 감지

    Returns:
        {
            "bigram_diversity": float,  # 0~1, 낮을수록 단조화
            "human_ratio": float,       # human 출처 비율
            "llm_ratio": float,
            "warnings": [str],
            "status": "healthy"|"warning"|"critical"
        }
    """
    l2 = _load_json(_L2_PATH)
    episodes = l2.get("episodes", [])
    recent = episodes[-DIVERSITY_SAMPLE_SIZE:] if len(episodes) >= DIVERSITY_SAMPLE_SIZE else episodes

    if not recent:
        return {"bigram_diversity": 1.0, "human_ratio": 1.0, "llm_ratio": 0.0,
                "warnings": [], "status": "healthy", "sample_size": 0}

    # bigram 다양성
    all_bigrams = []
    for ep in recent:
        content = ep.get("content", "")
        words = content.split()
        bigrams = [f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)]
        all_bigrams.extend(bigrams)

    bigram_diversity = len(set(all_bigrams)) / max(len(all_bigrams), 1)

    # 출처 비율
    human_count = sum(1 for ep in recent if ep.get("source", "human") == "human")
    llm_count = len(recent) - human_count
    human_ratio = human_count / len(recent)
    llm_ratio = llm_count / len(recent)

    # 경고 생성
    warnings = []
    if bigram_diversity < DIVERSITY_MIN_BIGRAM_RATIO:
        warnings.append(f"bigram 다양성 {bigram_diversity:.2f} < {DIVERSITY_MIN_BIGRAM_RATIO} — 응답 단조화 진행 중")
    if human_ratio < DIVERSITY_HUMAN_MIN_RATIO:
        warnings.append(f"human 출처 비율 {human_ratio:.0%} < {DIVERSITY_HUMAN_MIN_RATIO:.0%} — LLM 자기참조 루프 위험")
    if llm_ratio > 0.8:
        warnings.append(f"LLM 응답이 {llm_ratio:.0%} 점유 — Model Collapse 위험 높음")

    # 반복 구문 감지 (LLM 응답에서 흔히 발생)
    llm_eps = [ep for ep in recent if ep.get("source") == "llm"]
    if llm_eps:
        first_words = [ep.get("content", "")[:30] for ep in llm_eps]
        if len(set(first_words)) / max(len(first_words), 1) < 0.5:
            warnings.append("LLM 응답 시작 패턴 반복 감지 — 고정관념화 징후")

    if not warnings:
        status = "healthy"
    elif len(warnings) == 1:
        status = "warning"
    else:
        status = "critical"

    return {
        "bigram_diversity": round(bigram_diversity, 3),
        "human_ratio": round(human_ratio, 3),
        "llm_ratio": round(llm_ratio, 3),
        "sample_size": len(recent),
        "warnings": warnings,
        "status": status,
    }


def get_diversity_report() -> str:
    """diversity_check() 결과를 텔레그램용 텍스트로 반환."""
    d = diversity_check()
    status_icon = {"healthy": "✅", "warning": "⚠️", "critical": "🔴"}.get(d["status"], "❓")
    lines = [
        f"{status_icon} <b>메모리 다양성 진단</b> (최근 {d['sample_size']}개)",
        f"• Bigram 다양성: {d['bigram_diversity']:.0%}",
        f"• 출처 비율: 인간 {d['human_ratio']:.0%} / LLM {d['llm_ratio']:.0%}",
    ]
    if d["warnings"]:
        lines.append("\n<b>경고:</b>")
        for w in d["warnings"]:
            lines.append(f"  ⚠️ {w}")
    else:
        lines.append("  → 정상 범위")
    return "\n".join(lines)


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

    # Sycophancy Filter (2606.10949): 아첨·오류 동의 패턴 차단
    if role == "assistant":
        agreement_markers = ["맞아", "맞습니다", "맞네요", "정확해", "그렇네요",
                             "그렇죠", "맞죠", "당연히", "물론이죠", "당연하죠"]
        matched = [m for m in agreement_markers if m in text]
        if matched and len(text.strip()) < 80:
            return False, f"아첨 패턴 감지 ({', '.join(matched)}) — 사용자 오류 동조 위험"

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

    # 1. HORMA 계층 검색 (2606.11680): context_tags 클러스터 → 해당 클러스터 내 세부 검색
    # 전체 L2 브루트포스 대신 관련 클러스터만 검색 → 토큰 효율 향상
    try:
        from modules.bio_memory_engine import BioMemoryEngine, ImportanceScorer
        bio = BioMemoryEngine()
        l2_data = _load_json(_L2_PATH)
        all_episodes = l2_data.get("episodes", [])

        # Step 1: 쿼리 키워드로 관련 context_tags 클러스터 식별
        query_kws = set(ImportanceScorer.extract_keywords(query, max_kw=6))
        cluster_scores: dict[str, float] = {}
        for ep in all_episodes:
            for tag in ep.get("context_tags", []):
                overlap = len(query_kws & set(tag.lower().split()))
                cluster_scores[tag] = cluster_scores.get(tag, 0) + overlap + 1

        top_clusters = sorted(cluster_scores, key=cluster_scores.get, reverse=True)[:3]

        # Step 2: 상위 클러스터 에피소드만 세부 검색
        candidate_eps = [
            ep for ep in all_episodes
            if any(t in top_clusters for t in ep.get("context_tags", []))
        ] or all_episodes  # 클러스터 없으면 전체 fallback

        # Step 3: 키워드 유사도로 상위 K개 선택
        scored = []
        for ep in candidate_eps:
            ep_kws = set(ep.get("keywords", []))
            score = len(query_kws & ep_kws) + ep.get("importance", 1.0) * 0.1
            scored.append((score, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        l2_results = [ep for _, ep in scored[:top_k]]

        if l2_results:
            parts.append(f"🧠 [L2 클러스터 기억 | 클러스터: {', '.join(top_clusters[:2]) or '전체'}]")
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

    # 3. semantic_index.db FTS5 (00_Meta 위키 벡터 DB)
    try:
        if _SEMANTIC_DB.exists():
            import sqlite3, re as _re
            conn = sqlite3.connect(str(_SEMANTIC_DB))
            # FTS5 MATCH 쿼리: 단어 분리 후 OR 검색
            words = [w for w in _re.split(r'\s+', query.strip()) if len(w) >= 2][:6]
            if words:
                fts_query = " OR ".join(words)
                rows = conn.execute(
                    "SELECT path, snippet(fts_docs, 1, '', '', '...', 20) FROM fts_docs WHERE fts_docs MATCH ? LIMIT 3",
                    (fts_query,)
                ).fetchall()
                if rows:
                    parts.append("🗂 [시맨틱 인덱스]")
                    for path, snippet in rows:
                        doc = path.split("/")[-1] if path else "?"
                        parts.append(f"  · [{doc}] {snippet[:200]}")
            conn.close()
    except Exception as e:
        logger.warning(f"[MemRefine] semantic_index 검색 실패: {e}")

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
