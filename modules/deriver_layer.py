"""
Harness V2.5 — Deriver Layer (기억 파생/승격 엔진)
===================================================
Life-Harness Layer 2: L1 Working → L2 Episodic 승격 파이프라인

주요 기능:
- 중요도 스코어링 (ImportanceScorer)
- 에빙하우스 망각 곡선 계산 (ForgettingCurve)
- L1→L2 에피소드 승격 + 연상망 엣지 생성
- 키워드 추출 및 컨텍스트 태그 생성
- SemanticEngine (NPU 가속 임베딩) 연동
- 영구 장부 저장 (memory.md)

arXiv 2605.22166 — Memory Derivation Architecture
"""

import json
import re
import math
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger("DeriverLayer")

# ── 기본 상수 ──────────────────────────────────────────────
L1_TO_L2_THRESHOLD = 3.0
L2_TO_L3_REPEAT_COUNT = 3
FORGET_DAYS = 14
FORGET_IMPORTANCE_BELOW = 2.0
L1_MAX_SIZE = 30
L2_MAX_SIZE = 200


class ImportanceScorer:
    """메시지 중요도 스코어링 및 키워드/태그 추출."""

    HIGH_WEIGHT_KEYWORDS = [
        "오류", "에러", "버그", "수정", "긴급", "중요", "기억", "규칙",
        "설정", "비밀번호", "토큰", "API", "포트", "경로", "PATH",
        "실패", "복구", "치명", "항상", "절대", "반드시", "금지",
        "박사님", "승인", "결정", "확정", "완료", "배포", "/exec"
    ]
    MID_WEIGHT_KEYWORDS = [
        "일정", "작업", "TODO", "예약", "스케줄", "모듈", "스크립트",
        "파일", "폴더", "위키", "저장", "업데이트", "변경", "추가",
        "삭제", "이동", "생성", "분석", "검색", "질문"
    ]
    EMOTION_PATTERNS = [r"!{2,}", r"중요.*:", r"주의.*:", r"경고.*:", r"\[긴급\]", r"\[중요\]"]
    TAG_RULES = {
        "시스템": ["lm studio", "포트", "서버", "재시작", "에러", "오류"],
        "파일작업": ["파일", "폴더", "이동", "복사", "생성", "삭제"],
        "연구": ["논문", "연구", "분석", "데이터", "프롬프트", "claude"],
        "트레이딩": ["주식", "매수", "매도", "rsi", "ema", "sepa", "백테스트"]
    }

    @classmethod
    def load_config(cls, config_path: Path):
        """설정 파일에서 동적으로 가중치 단어 목록과 태그 규칙을 불러옵니다."""
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                cls.HIGH_WEIGHT_KEYWORDS = data.get("high_weight_keywords", cls.HIGH_WEIGHT_KEYWORDS)
                cls.MID_WEIGHT_KEYWORDS = data.get("mid_weight_keywords", cls.MID_WEIGHT_KEYWORDS)
                cls.EMOTION_PATTERNS = data.get("emotion_patterns", cls.EMOTION_PATTERNS)
                cls.TAG_RULES = data.get("tag_rules", cls.TAG_RULES)
                logger.info(f"💾 [Deriver] 외부 설정 로드 완료: {config_path}")
            except Exception as e:
                logger.error(f"⚠️ [Deriver] 외부 설정 로드 실패 (기본값 사용): {e}")

    @classmethod
    def score(cls, text: str, role: str = "user") -> float:
        """메시지 중요도 점수 산출 (1.0~10.0)."""
        score = 1.0
        if role == "assistant":
            score = 0.5
        text_lower = text.lower()
        for kw in cls.HIGH_WEIGHT_KEYWORDS:
            if kw.lower() in text_lower:
                score += 2.0
                break
        mid_count = sum(1 for kw in cls.MID_WEIGHT_KEYWORDS if kw.lower() in text_lower)
        score += min(mid_count * 1.0, 3.0)
        for pat in cls.EMOTION_PATTERNS:
            if re.search(pat, text):
                score += 1.5
                break
        if len(text) > 300:
            score += 0.5
        if len(text) > 800:
            score += 0.5
        return round(min(score, 10.0), 2)

    @classmethod
    def extract_keywords(cls, text: str, max_kw: int = 5) -> List[str]:
        """텍스트에서 핵심 키워드 추출."""
        stopwords = {"이", "그", "저", "을", "를", "이다", "있다", "하다",
                     "않다", "것", "수", "등", "및"}
        words = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text)
        freq = {}
        for w in words:
            if w.lower() not in stopwords:
                freq[w] = freq.get(w, 0) + 1
        return sorted(freq, key=freq.get, reverse=True)[:max_kw]

    @classmethod
    def generate_context_tags(cls, text: str) -> List[str]:
        """텍스트에서 컨텍스트 태그 생성."""
        tags = []
        text_lower = text.lower()
        for tag, kws in cls.TAG_RULES.items():
            if any(kw in text_lower for kw in kws):
                tags.append(tag)
        return tags


class ForgettingCurve:
    """에빙하우스 망각 곡선 기반 보유율/소멸 결정."""

    STABILITY_FACTOR = 5.0

    @classmethod
    def retention(cls, importance: float, last_accessed_iso: str) -> float:
        """지정된 중요도와 마지막 접근 시간을 기반으로 기억 보유율(0~1) 계산."""
        try:
            last = datetime.fromisoformat(last_accessed_iso)
        except Exception:
            last = datetime.now(timezone.utc)
        days_elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        stability = importance * cls.STABILITY_FACTOR
        return round(math.exp(-days_elapsed / max(stability, 0.1)), 4)

    @classmethod
    def should_forget(cls, importance: float, last_accessed_iso: str) -> bool:
        """지정된 기억을 소멸해야 하는지 판단."""
        if importance >= FORGET_IMPORTANCE_BELOW:
            return False
        try:
            last = datetime.fromisoformat(last_accessed_iso)
        except Exception:
            return False
        days_elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        return days_elapsed >= FORGET_DAYS


# ── JSON 유틸리티 ──────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"episodes": []}


def _save_json(path: Path, data: dict):
    """원자적(Atomic) 파일 저장."""
    temp_path = path.with_suffix('.tmp')
    try:
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    except Exception as e:
        logger.error(f"❌ [Deriver] 원자적 파일 저장 실패 ({path.name}): {e}")
        if temp_path.exists():
            temp_path.unlink()


# ── Deriver Engine ─────────────────────────────────────────

class DeriverEngine:
    """
    L1→L2 기억 파생/승격 엔진.
    - L1 (Working Memory): 최근 대화 버퍼
    - L2 (Episodic Cache): 중요 에피소드 + 연상망
    - SemanticEngine 연동: NPU 가속 임베딩
    """

    def __init__(self, vault_path: str = "/Users/bluesea/Applications/Mjobsidian"):
        self.vault_path = Path(vault_path)
        self.meta_dir = self.vault_path / "wiki" / "00_Meta"
        self.memory_file = str(self.meta_dir / "memory.md")

        # 메모리 파일 경로
        bio_dir = Path("/Users/bluesea/.hermes/memory")
        bio_dir.mkdir(parents=True, exist_ok=True)
        self.l1_path = Path("/Users/bluesea/Applications/Mjauto/Scripts/harness_memory.json")
        self.l2_path = bio_dir / "episodic_memory.json"
        self.config_path = bio_dir / "bio_memory_config.json"

        # 설정 로드
        self._ensure_config()
        ImportanceScorer.load_config(self.config_path)

        # L2 구조 보장
        self._ensure_structures()

        # SemanticEngine 연동
        self._sem_engine = None
        self._init_semantic_engine()

    def _ensure_config(self):
        """기본 설정 파일이 없을 경우 생성."""
        if not self.config_path.exists():
            default_config = {
                "high_weight_keywords": ImportanceScorer.HIGH_WEIGHT_KEYWORDS,
                "mid_weight_keywords": ImportanceScorer.MID_WEIGHT_KEYWORDS,
                "emotion_patterns": ImportanceScorer.EMOTION_PATTERNS,
                "tag_rules": ImportanceScorer.TAG_RULES,
            }
            _save_json(self.config_path, default_config)

    def _ensure_structures(self):
        """L2 JSON 구조 초기화."""
        if not self.l2_path.exists():
            _save_json(self.l2_path, {"version": "1.1", "episodes": [], "associations": {}})
        else:
            l2 = _load_json(self.l2_path)
            if "associations" not in l2:
                l2["associations"] = {}
                l2["version"] = "1.1"
                _save_json(self.l2_path, l2)

    def _init_semantic_engine(self):
        """SemanticEngine (NPU 가속 임베딩) 초기화."""
        try:
            from MacBot.semantic_engine import SemanticEngine
            self._sem_engine = SemanticEngine(str(self.vault_path))
            logger.info("⚡ [Deriver] MacBot SemanticEngine 결합 성공 (NPU 가속 임베딩).")
        except ImportError:
            try:
                from modules.semantic_engine import SemanticEngine
                self._sem_engine = SemanticEngine(str(self.vault_path))
                logger.info("⚡ [Deriver] modules SemanticEngine 결합 성공 (NPU 가속 임베딩).")
            except ImportError:
                self._sem_engine = None
                logger.warning("⚠️ [Deriver] SemanticEngine 로드 실패. 키워드 기반 연상 검색으로 대체.")

    # ── L1→L2 처리 ─────────────────────────────────────────

    def add_message(self, role: str, content: str):
        """메시지를 L1에 추가하고, 중요도 임계치 초과 시 L2로 자동 승격."""
        now_iso = datetime.now(timezone.utc).isoformat()
        importance = ImportanceScorer.score(content, role)

        # L1 로드 및 추가
        l1_data = []
        if self.l1_path.exists():
            try:
                l1_data = json.loads(self.l1_path.read_text(encoding="utf-8"))
            except Exception:
                l1_data = []

        new_entry = {
            "role": role, "content": content,
            "timestamp": now_iso, "importance": importance,
        }
        l1_data.append(new_entry)

        # L1 용량 초과 시 오래된/낮은 중요도 항목 L2 승격
        if len(l1_data) > L1_MAX_SIZE:
            l1_data.sort(key=lambda x: (x.get("importance", 1.0), x.get("timestamp", "")))
            removed = l1_data.pop(0)
            if removed.get("importance", 0) >= L1_TO_L2_THRESHOLD:
                self._promote_to_l2(removed)

        _save_json(self.l1_path, l1_data)

        # 중요도가 높은 항목은 즉시 L2 승격
        if importance >= L1_TO_L2_THRESHOLD:
            self._promote_to_l2(new_entry)

    def _promote_to_l2(self, entry: dict):
        """L1 항목을 L2 에피소드로 승격 + 연상망 엣지 생성."""
        l2 = _load_json(self.l2_path)
        episodes = l2.get("episodes", [])
        associations = l2.get("associations", {})

        # 임베딩 생성
        embedding = None
        if self._sem_engine:
            try:
                embedding = self._sem_engine.get_embedding(entry["content"])
            except Exception as e:
                logger.warning(f"⚠️ [Deriver] 임베딩 생성 실패: {e}")

        new_id = f"ep_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]}"
        keywords = ImportanceScorer.extract_keywords(entry["content"])

        episode = {
            "id": new_id,
            "role": entry.get("role", "unknown"),
            "content": entry["content"],
            "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "importance": entry.get("importance", 1.0),
            "last_accessed": datetime.now(timezone.utc).isoformat(),
            "access_count": 1,
            "keywords": keywords,
            "context_tags": ImportanceScorer.generate_context_tags(entry["content"]),
            "embedding": embedding,
        }

        # 연상망 엣지 생성
        associations[new_id] = []

        # 1. 시간차 연결 (Temporal Edge)
        if episodes:
            prev_ep = episodes[-1]
            prev_id = prev_ep["id"]
            associations[new_id].append({"target": prev_id, "weight": 1.0, "type": "temporal"})
            if prev_id not in associations:
                associations[prev_id] = []
            associations[prev_id].append({"target": new_id, "weight": 1.0, "type": "temporal"})

        # 2. 키워드 공유 연결 (Semantic Keyword Edge)
        new_kws = set(keywords)
        if new_kws:
            for other_ep in episodes:
                other_id = other_ep["id"]
                other_kws = set(other_ep.get("keywords", []))
                shared = new_kws & other_kws
                if shared:
                    weight = round(min(len(shared) * 0.4, 1.0), 2)
                    associations[new_id].append({"target": other_id, "weight": weight, "type": "keyword"})
                    if other_id not in associations:
                        associations[other_id] = []
                    associations[other_id].append({"target": new_id, "weight": weight, "type": "keyword"})

        episodes.append(episode)

        # L2 용량 초과 시 망각 곡선 따라 정리
        if len(episodes) > L2_MAX_SIZE:
            episodes.sort(key=lambda x: ForgettingCurve.retention(
                x.get("importance", 1.0),
                x.get("last_accessed", x.get("timestamp", ""))
            ))
            removed = episodes.pop(0)
            removed_id = removed["id"]
            # Dreamer Layer에 L3 후보 통지
            self._notify_dreamer_candidate(removed)
            # 연상망 엣지 정화
            if removed_id in associations:
                del associations[removed_id]
            for src_id, targets in list(associations.items()):
                associations[src_id] = [t for t in targets if t["target"] != removed_id]

        l2["episodes"] = episodes
        l2["associations"] = associations
        _save_json(self.l2_path, l2)

    def _notify_dreamer_candidate(self, episode: dict):
        """Dreamer Layer에 L3 후보 통보 (임포트 게으름 방지)."""
        try:
            from dreamer_layer import DreamerEngine
            # 순환 임포트 방지를 위한 지연 호출
            de = DreamerEngine()
            de.note_l3_candidate(episode)
        except ImportError:
            # Dreamer Layer가 없으면 L3에 직접 저장
            self._note_l3_fallback(episode)
        except Exception as e:
            logger.error(f"⚠️ [Deriver] Dreamer 통보 실패 (무시): {e}")

    def _note_l3_fallback(self, episode: dict):
        """Dreamer Layer 없을 때 L3 직접 저장."""
        l3_path = Path("/Users/bluesea/.hermes/memory/semantic_memory.json")
        l3 = _load_json(l3_path) if l3_path.exists() else {"patterns": [], "user_model": {
            "preferences": [], "frequent_topics": {}, "system_rules": []}, "procedural": {}}
        patterns = l3.get("patterns", [])
        ep_keywords = set(episode.get("keywords", []))
        merged = False
        for pat in patterns:
            if len(ep_keywords & set(pat.get("keywords", []))) >= 2:
                pat["frequency"] = pat.get("frequency", 1) + 1
                pat["last_seen"] = datetime.now(timezone.utc).isoformat()
                merged = True
                break
        if not merged:
            patterns.append({
                "id": f"pat_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "summary": episode["content"][:200],
                "keywords": episode.get("keywords", []),
                "frequency": 1,
                "importance": episode.get("importance", 1.0),
                "last_seen": datetime.now(timezone.utc).isoformat(),
            })
        l3["patterns"] = patterns
        _save_json(l3_path, l3)

    # ── 영구 장부 저장 ──────────────────────────────────────

    def save_important(self, key: str, val: str) -> str:
        """핵심 정보를 memory.md와 L2/L3에 동시 저장."""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.write(f"- [{timestamp}] [{key}] {val}\n")
            self._promote_to_l2({
                "role": "system",
                "content": f"[{key}] {val}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "importance": 8.0,
            })
            return "✅ 핵심 영구 장부(memory.md) 및 L2/L3 계층 동시 인코딩 완료."
        except Exception as e:
            return f"❌ 기억 저장 중 물리적 디스크 에러: {e}"

    # ── 상태 및 유틸리티 ────────────────────────────────────

    def get_status(self) -> dict:
        """Deriver Layer 상태 정보 반환."""
        l1 = []
        if self.l1_path.exists():
            try:
                l1 = json.loads(self.l1_path.read_text(encoding="utf-8"))
            except Exception:
                l1 = []
        l2 = _load_json(self.l2_path)
        episodes = l2.get("episodes", [])
        associations = l2.get("associations", {})
        edge_count = sum(len(targets) for targets in associations.values()) // 2
        forget_candidates = sum(
            1 for ep in episodes
            if ForgettingCurve.should_forget(
                ep.get("importance", 1.0),
                ep.get("last_accessed", ep.get("timestamp", ""))
            )
        )
        return {
            "l1_count": len(l1),
            "l1_max": L1_MAX_SIZE,
            "l2_count": len(episodes),
            "l2_max": L2_MAX_SIZE,
            "edges": edge_count,
            "forget_candidates": forget_candidates,
            "sem_engine": self._sem_engine is not None,
        }

    def get_l2_data(self) -> dict:
        """L2 전체 데이터 반환 (Dialectic Layer에서 사용)."""
        return _load_json(self.l2_path)

    def get_l1_data(self) -> list:
        """L1 데이터 반환."""
        if self.l1_path.exists():
            try:
                return json.loads(self.l1_path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []
