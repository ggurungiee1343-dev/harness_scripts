"""
memory_consolidator_v2.py — L2→L3 증류 파이프라인 (실제 데이터 기반 수정판)

v1 대비 수정사항:
  ❌ v1: L2 경로 ~/.hermes/episodic_memory.json (잘못됨)
  ✅ v2: ~/.hermes/memory/episodic_memory.json

  ❌ v1: L3 경로 modules/semantic_memory.json (잘못됨)
  ✅ v2: ~/.hermes/memory/semantic_memory.json

  ❌ v1: SemanticPattern 자체 포맷 (bio_memory_engine과 비호환)
  ✅ v2: bio_memory_engine._note_l3_candidate()와 동일 포맷
         {id, summary, keywords, frequency, importance, last_seen}

  ❌ v1: importance 기준 4.0 (실측 L2는 6.0~7.0)
  ✅ v2: promote_importance_min = 5.0

  ✅ v2 추가: context_tags → 카테고리 매핑 (L2에 이미 있음)
  ✅ v2 추가: Lock Stack 우회 없음 (bio_memory_engine 로직 동일 적용)
"""

import json, os, sys, math, logging, argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Consolidator")

# ── 실제 경로 ─────────────────────────────────────────────────────────────────
_BIO_DIR   = Path("/Users/bluesea/.hermes/memory")
L2_PATH    = _BIO_DIR / "episodic_memory.json"
L3_PATH    = _BIO_DIR / "semantic_memory.json"
STATE_PATH = Path("/Users/bluesea/Applications/Mjauto/Scripts/hermes/memory_engine/consolidator_state.json")
LOG_PATH   = Path("/Users/bluesea/Applications/Mjauto/Scripts/logs/consolidation.log")

# ── 정책 상수 ──────────────────────────────────────────────────────────────────
POLICY = {
    "promote_importance_min": 5.0,   # 실측 6.0~7.0 → 5.0으로 설정
    "drop_retention_below":   0.15,  # memory_refinement.py 기준값
    "forget_min_age_days":    7,     # memory_refinement.py 기준값
    "max_l2_items":           200,   # bio_memory_engine.py: L2_MAX_SIZE
    "min_content_len":        50,
}

# ── context_tags → category 매핑 (L2에 이미 분류되어 있음) ───────────────────
TAG_TO_CATEGORY = {
    "시스템":   "system",
    "연구":     "research",
    "파일작업": "system",
    "트레이딩": "stock",
}


class ForgettingCurve:
    """bio_memory_engine.py의 ForgettingCurve와 동일"""
    STABILITY_FACTOR = 5.0

    @classmethod
    def retention(cls, importance: float, last_accessed_iso: str) -> float:
        try:
            last = datetime.fromisoformat(last_accessed_iso)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except:
            last = datetime.now(timezone.utc)
        days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        stability = importance * cls.STABILITY_FACTOR
        return round(math.exp(-days / max(stability, 0.1)), 4)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"JSON 로드 실패 {path}: {e}")
        return {}


def _save_json(path: Path, data: dict) -> None:
    """Atomic write"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(path))


def _classify_category(episode: dict) -> str:
    """context_tags 우선 사용, 없으면 키워드로 분류"""
    for tag in episode.get("context_tags", []):
        if tag in TAG_TO_CATEGORY:
            return TAG_TO_CATEGORY[tag]
    text = " ".join(episode.get("keywords", [])).lower()
    text += episode.get("content", "")[:100].lower()
    if any(w in text for w in ["해양", "선박", "도선", "maritime"]): return "maritime_law"
    if any(w in text for w in ["주식", "삼돌이", "매수", "stock"]):  return "stock"
    if any(w in text for w in ["논문", "arxiv", "paper", "연구"]):   return "research"
    if any(w in text for w in ["hermes", "하네스", "메모리"]):        return "system"
    return "general"


class ConsolidationResult(NamedTuple):
    total_loaded:   int
    young_skip:     int
    low_ret_drop:   int
    promoted_to_l3: int
    remaining_l2:   int
    duration_sec:   float


def consolidate(dry_run: bool = False) -> ConsolidationResult:
    t0      = datetime.now()
    now_utc = datetime.now(timezone.utc)
    log     = print

    log("=" * 55)
    log(f"🧠 Hermes L2→L3 증류 v2 | {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    if dry_run: log("  [DRY RUN]")
    log("=" * 55)

    # Step 1. L2 로드
    l2_data  = _load_json(L2_PATH)
    episodes = l2_data.get("episodes", [])
    total_loaded = len(episodes)
    log(f"\nStep1. L2 로드: {total_loaded}개 ({L2_PATH})")
    if not episodes:
        log("  에피소드 없음. 종료.")
        return ConsolidationResult(0,0,0,0,0,0.0)

    # Step 2. 최소 나이 미달 제외 (너무 최신 항목은 아직 증류 보류)
    def too_young(ep):
        try:
            t = datetime.fromisoformat(ep.get("timestamp", ""))
            if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
            return (now_utc - t).days < POLICY["forget_min_age_days"]
        except: return False
    active     = [e for e in episodes if not too_young(e)]
    young_skip = total_loaded - len(active)
    log(f"Step2. {POLICY['forget_min_age_days']}일 미만 제외: {young_skip}개 → {len(active)}개")

    # Step 3. 보유율 낮은 항목 제거
    kept, low_ret_drop = [], 0
    for ep in active:
        r = ForgettingCurve.retention(
            ep.get("importance", 1.0),
            ep.get("last_accessed", ep.get("timestamp", ""))
        )
        ep["_ret"] = r
        if r >= POLICY["drop_retention_below"]:
            kept.append(ep)
        else:
            low_ret_drop += 1
    log(f"Step3. 저보유율 제거: {low_ret_drop}개 → {len(kept)}개")

    # Step 4. 승격 후보
    cands = [
        e for e in kept
        if e.get("importance", 0) >= POLICY["promote_importance_min"]
        and len(e.get("content", "")) >= POLICY["min_content_len"]
    ]
    log(f"Step4. 승격 후보: {len(cands)}개 (importance≥{POLICY['promote_importance_min']})")

    # Step 5. L3 저장 (bio_memory_engine._note_l3_candidate()와 동일 로직)
    l3_data  = _load_json(L3_PATH)
    patterns = l3_data.get("patterns", [])
    promoted, promoted_ids = 0, set()

    for ep in cands:
        ep_kws   = set(ep.get("keywords", []))
        category = _classify_category(ep)
        merged   = False

        # 키워드 2개 이상 겹치면 기존 패턴에 merge (bio_memory_engine 동일)
        for pat in patterns:
            if len(ep_kws & set(pat.get("keywords", []))) >= 2:
                pat["frequency"] = pat.get("frequency", 1) + 1
                pat["last_seen"] = now_utc.isoformat()
                pat["importance"] = max(pat.get("importance", 0), ep.get("importance", 0))
                pat.setdefault("category", category)  # 없으면 추가, 있으면 유지
                merged = True
                break

        if not merged:
            patterns.append({
                "id":         f"pat_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]}",
                "summary":    ep["content"][:200],   # bio_memory_engine과 동일
                "keywords":   ep.get("keywords", []),
                "frequency":  1,
                "importance": ep.get("importance", 1.0),
                "last_seen":  now_utc.isoformat(),
                "category":   category,              # v2 추가 (하위 호환)
                "source_ep":  ep["id"],              # v2 추가 (추적용)
            })

        promoted += 1
        promoted_ids.add(ep["id"])
        log(f"  ✅ [{category}] importance={ep['importance']} | {ep['content'][:50]}...")

    if not dry_run:
        l3_data["patterns"] = patterns
        _save_json(L3_PATH, l3_data)
    log(f"\nStep5. L3 완료: {promoted}개 승격 | 총 패턴: {len(patterns)}개")

    # Step 6. L2 다이어트
    remaining = [
        {k: v for k, v in ep.items() if k != "_ret"}
        for ep in kept if ep["id"] not in promoted_ids
    ]
    log(f"Step6. L2 다이어트: {len(kept)-len(remaining)}개 제거 → {len(remaining)}개")

    if len(remaining) > POLICY["max_l2_items"]:
        remaining.sort(
            key=lambda e: ForgettingCurve.retention(
                e.get("importance",1.0), e.get("last_accessed", e.get("timestamp",""))
            ), reverse=True
        )
        remaining = remaining[:POLICY["max_l2_items"]]

    if not dry_run:
        l2_data["episodes"] = remaining
        _save_json(L2_PATH, l2_data)

    # Step 7. 상태 저장
    dur = (datetime.now() - t0).total_seconds()
    result = ConsolidationResult(total_loaded, young_skip, low_ret_drop,
                                  promoted, len(remaining), round(dur, 2))
    if not dry_run:
        _save_json(STATE_PATH, {
            "last_run":    now_utc.isoformat(),
            "last_result": result._asdict(),
            "policy":      POLICY,
        })

    log(f"\n✨ 완료 {dur:.1f}초 | 로드:{total_loaded} 남음:{len(remaining)} L3승격:{promoted}")
    return result


def health_check() -> str:
    l2  = _load_json(L2_PATH)
    l3  = _load_json(L3_PATH)
    st  = _load_json(STATE_PATH)
    eps = l2.get("episodes", [])
    pts = l3.get("patterns", [])

    promotable = sum(1 for e in eps
                     if e.get("importance",0) >= POLICY["promote_importance_min"]
                     and len(e.get("content","")) >= POLICY["min_content_len"])
    low_ret    = sum(1 for e in eps
                     if ForgettingCurve.retention(
                         e.get("importance",1.0),
                         e.get("last_accessed", e.get("timestamp",""))
                     ) < POLICY["drop_retention_below"])
    cat_dist: dict = {}
    for p in pts:
        c = p.get("category","general")
        cat_dist[c] = cat_dist.get(c, 0) + 1

    lines = [
        "🧠 Hermes 메모리 상태",
        f"  L2: {len(eps)}개 | 승격가능: {promotable} | 저보유율: {low_ret}",
        f"  L3: {len(pts)}개 패턴 + {len(l3.get('procedural',{}))}개 절차",
    ]
    for cat, cnt in cat_dist.items():
        lines.append(f"    [{cat}]: {cnt}개")
    lines.append(f"  마지막 증류: {st.get('last_run', '미실행')}")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--health",  action="store_true")
    args = parser.parse_args()
    if args.health:
        print(health_check())
    else:
        consolidate(dry_run=args.dry_run)
