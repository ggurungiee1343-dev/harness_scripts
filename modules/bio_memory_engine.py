import os
import json
import math
import re
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

sys.path.append("/Users/bluesea/Applications/Mjauto/Scripts")
sys.path.append("/Users/bluesea/Applications/Mjauto/Scripts/modules")
logger = logging.getLogger("BioMemoryEngine")

L1_TO_L2_THRESHOLD = 3.0
L2_TO_L3_REPEAT_COUNT = 3
FORGET_DAYS = 14
FORGET_IMPORTANCE_BELOW = 2.0
L1_MAX_SIZE = 30
L2_MAX_SIZE = 200
L2_MAX_BYTES = 1 * 1024 * 1024  # 1MB — 용량 기반 L3 전이 임계값

# ── Layer Pre-Query Cache ────────────────────────────────────
QUERY_CACHE_TTL = 3600  # 1시간
_query_cache: Dict[str, tuple] = {}  # {query_key: (timestamp, result)}

class ImportanceScorer:
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
                logger.info(f"💾 [Bio-Memory] 외부 설정 로드 완료: {config_path}")
            except Exception as e:
                logger.error(f"⚠️ [Bio-Memory] 외부 설정 로드 실패 (기본값 사용): {e}")

    @classmethod
    def score(cls, text: str, role: str = "user") -> float:
        score = 1.0
        if role == "assistant": score = 0.5
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
        if len(text) > 300: score += 0.5
        if len(text) > 800: score += 0.5
        result = round(min(score, 10.0), 2)
        # Model Collapse 방어 (Oxford 2305.17493): LLM 응답이 L2에 자동 승격되면
        # 자기 응답을 재참조하는 피드백 루프 발생 → assistant는 임계값 미만으로 하드캡
        if role == "assistant":
            result = min(result, L1_TO_L2_THRESHOLD - 0.1)
        return result

    @classmethod
    def extract_keywords(cls, text: str, max_kw: int = 5) -> List[str]:
        stopwords = {"이", "그", "저", "을", "를", "이다", "있다", "하다", "않다", "것", "수", "등", "및"}
        words = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text)
        freq = {}
        for w in words:
            if w.lower() not in stopwords:
                freq[w] = freq.get(w, 0) + 1
        return sorted(freq, key=freq.get, reverse=True)[:max_kw]

class ForgettingCurve:
    STABILITY_FACTOR = 5.0

    @classmethod
    def retention(cls, importance: float, last_accessed_iso: str) -> float:
        try: last = datetime.fromisoformat(last_accessed_iso)
        except: last = datetime.now(timezone.utc)
        days_elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        stability = importance * cls.STABILITY_FACTOR
        return round(math.exp(-days_elapsed / max(stability, 0.1)), 4)

    @classmethod
    def should_forget(cls, importance: float, last_accessed_iso: str) -> bool:
        if importance >= FORGET_IMPORTANCE_BELOW: return False
        try: last = datetime.fromisoformat(last_accessed_iso)
        except: return False
        days_elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        return days_elapsed >= FORGET_DAYS

class BioMemoryEngine:
    def __init__(self, vault_path: str = "/Users/bluesea/Applications/Mjobsidian"):
        self.vault_path = Path(vault_path)
        self.meta_dir = self.vault_path / "wiki" / "00_Meta"
        self.memory_file = str(self.meta_dir / "memory.md")
        self.hot_file = str(self.meta_dir / "hot.md")

        bio_dir = Path("/Users/bluesea/.hermes/memory")
        bio_dir.mkdir(parents=True, exist_ok=True)
        self.l1_path = Path("/Users/bluesea/Applications/Mjauto/Scripts/harness_memory.json")
        self.l2_path = bio_dir / "episodic_memory.json"
        self.l3_path = bio_dir / "semantic_memory.json"
        
        # 설정 파일 경로 정의 및 로드
        self.config_path = bio_dir / "bio_memory_config.json"
        self._ensure_config()
        ImportanceScorer.load_config(self.config_path)

        self._ensure_structures()
        self.vim = None  # VectorIndexManager — __post_init__ 에서 바인딩

        # 실제 MacBot의 SemanticEngine 로드 연동 (FastEmbed NPU 임베딩 가속 지원)
        try:
            from MacBot.semantic_engine import SemanticEngine
            self.sem_engine = SemanticEngine(str(self.vault_path))
            logger.info("⚡ [Bio-Memory] MacBot SemanticEngine 결합 성공 (NPU 가속 임베딩 활성화).")
        except ImportError:
            try:
                from modules.semantic_engine import SemanticEngine
                self.sem_engine = SemanticEngine(str(self.vault_path))
                logger.info("⚡ [Bio-Memory] modules SemanticEngine 결합 성공 (NPU 가속 임베딩 활성화).")
            except ImportError:
                self.sem_engine = None
                logger.warning("⚠️ [Bio-Memory] SemanticEngine 로드 실패. 키워드 기반 연상 검색으로 대체합니다.")

        # 인라인 임베딩 마이그레이션: 기존 JSON 내 embedding 필드 제거 → turbovec 이관
        # Code as Agent Harness §3.2.6 State Offloading: 무거운 상태는 외부 인덱스로
        self._strip_inline_embeddings()

    def _strip_inline_embeddings(self):
        """기존 L2 에피소드의 인라인 embedding 필드 제거 → turbovec 외부 인덱스로 이관.

        배경: embedding(384 float)을 JSON에 저장하면 에피소드당 ~3KB 과부하.
        200개 × 3KB = 600KB+ → episodic_memory.json 비대화 원인.
        State Offloading (Code as Agent Harness §3.2.6) 적용.
        """
        try:
            l2 = self._load_json(self.l2_path)
            episodes = l2.get("episodes", [])
            migrated = 0
            for ep in episodes:
                if "embedding" in ep:
                    emb = ep.pop("embedding")
                    if emb and self.vim:
                        try:
                            self.vim.add(ep["id"], emb)
                        except Exception:
                            pass
                    migrated += 1
            if migrated:
                self._save_json(self.l2_path, l2)
                logger.info(f"[Bio-Memory] 인라인 임베딩 {migrated}개 제거 완료 (turbovec 이관)")
        except Exception as e:
            logger.warning(f"[Bio-Memory] 임베딩 마이그레이션 실패: {e}")

    def _ensure_config(self):
        """기본 설정 파일이 없을 경우 생성합니다."""
        if not self.config_path.exists():
            default_config = {
                "high_weight_keywords": ImportanceScorer.HIGH_WEIGHT_KEYWORDS,
                "mid_weight_keywords": ImportanceScorer.MID_WEIGHT_KEYWORDS,
                "emotion_patterns": ImportanceScorer.EMOTION_PATTERNS,
                "tag_rules": ImportanceScorer.TAG_RULES
            }
            self._save_json(self.config_path, default_config)

    def _ensure_structures(self):
        if not self.l2_path.exists():
            self._save_json(self.l2_path, {"version": "1.1", "episodes": [], "associations": {}})
        else:
            l2 = self._load_json(self.l2_path)
            if "associations" not in l2:
                l2["associations"] = {}
                l2["version"] = "1.1"
                self._save_json(self.l2_path, l2)

        if not self.l3_path.exists():
            self._save_json(self.l3_path, {"version": "1.1", "patterns": [], "user_model": {"preferences": [], "frequent_topics": {}, "system_rules": []}, "procedural": {}})
        else:
            l3 = self._load_json(self.l3_path)
            if "procedural" not in l3:
                l3["procedural"] = {}
                l3["version"] = "1.1"
                self._save_json(self.l3_path, l3)

    def _load_json(self, path: Path) -> dict:
        try: return json.loads(path.read_text(encoding="utf-8"))
        except: return {"episodes": []} if "episodic" in path.name else {"patterns": []}

    def _save_json(self, path: Path, data: dict):
        """파일 쓰기 충돌 방지를 위해 원자적(Atomic)으로 안전하게 덮어씁니다."""
        temp_path = path.with_suffix('.tmp')
        try:
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
        except Exception as e:
            logger.error(f"❌ [Bio-Memory] 원자적 파일 저장 실패 ({path.name}): {e}")
            if temp_path.exists():
                temp_path.unlink()

    def add_message(self, role: str, content: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        importance = ImportanceScorer.score(content, role)
        
        l1_data = []
        if self.l1_path.exists():
            try: l1_data = json.loads(self.l1_path.read_text(encoding="utf-8"))
            except: l1_data = []

        new_entry = {"role": role, "content": content, "timestamp": now_iso, "importance": importance}
        l1_data.append(new_entry)

        if len(l1_data) > L1_MAX_SIZE:
            l1_data.sort(key=lambda x: (x.get("importance", 1.0), x.get("timestamp", "")))
            removed = l1_data.pop(0)
            if removed.get("importance", 0) >= L1_TO_L2_THRESHOLD:
                self._promote_to_l2(removed)

        self._save_json(self.l1_path, l1_data)
        if importance >= L1_TO_L2_THRESHOLD:
            self._promote_to_l2(new_entry)

    def _promote_to_l2(self, entry: dict):
        l2 = self._load_json(self.l2_path)
        episodes = l2.get("episodes", [])
        associations = l2.get("associations", {})
        
        # 임베딩 생성 (SemanticEngine 활용)
        embedding = None
        if self.sem_engine:
            try:
                embedding = self.sem_engine.get_embedding(entry["content"])
            except Exception as e:
                logger.warning(f"⚠️ [Bio-Memory] 임베딩 생성 실패: {e}")
                
        new_id = f"ep_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]}"
        keywords = ImportanceScorer.extract_keywords(entry["content"])
        role = entry.get("role", "unknown")

        episode = {
            "id": new_id,
            "role": role,
            # Model Collapse 방어 (Oxford 2305.17493): 출처 태깅
            # 컨텍스트 재주입 시 human 항목 우선 보장에 사용
            "source": "human" if role == "user" else "llm",
            "content": entry["content"],
            "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "importance": entry.get("importance", 1.0),
            "last_accessed": datetime.now(timezone.utc).isoformat(),
            "access_count": 1,
            "keywords": keywords,
            "context_tags": self._generate_context_tags(entry["content"]),
            # embedding은 JSON에 저장하지 않음 → turbovec 외부 인덱스 사용
            # (State Offloading: Code as Agent Harness §3.2.6)
        }
        
        # turbovec 외부 인덱스에 임베딩 등록 (JSON 인라인 저장 대신)
        if embedding:
            if self.vim:
                try:
                    self.vim.add(new_id, embedding)
                except Exception as e:
                    logger.warning(f"[Bio-Memory] turbovec 등록 실패: {e}")
            # vim 없을 경우에만 fallback으로 인라인 저장 (이 경우는 SemanticEngine도 없는 상태)
            else:
                episode["embedding"] = embedding

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

        # Hybrid Trigger: while 루프로 L3 전이 — if → while 수정
        # 기존 if는 1개씩만 제거 → 이미 2.3MB인 파일에 무효. while로 기준치 복원까지 반복.
        # _get_l2_bytes()로 실제 직렬화 크기 계산 (stat().st_size는 디스크 기준이라 부정확)
        while len(episodes) > L2_MAX_SIZE or self._get_l2_bytes(episodes, associations) > L2_MAX_BYTES:
            episodes.sort(key=lambda x: ForgettingCurve.retention(x.get("importance", 1.0), x.get("last_accessed", x.get("timestamp", ""))))
            removed = episodes.pop(0)
            removed_id = removed["id"]
            self._note_l3_candidate(removed)
            # turbovec 인덱스에서도 제거
            if self.vim:
                try:
                    self.vim.remove(removed_id)
                except Exception:
                    pass
            # 연상망 엣지 정화
            if removed_id in associations:
                del associations[removed_id]
            for src_id, targets in list(associations.items()):
                associations[src_id] = [t for t in targets if t["target"] != removed_id]

        l2["episodes"] = episodes
        l2["associations"] = associations
        self._save_json(self.l2_path, l2)

    def _generate_context_tags(self, text: str) -> List[str]:
        tags = []
        text_lower = text.lower()
        for tag, kws in ImportanceScorer.TAG_RULES.items():
            if any(kw in text_lower for kw in kws): tags.append(tag)
        return tags

    def _note_l3_candidate(self, episode: dict):
        l3 = self._load_json(self.l3_path)
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
                "last_seen": datetime.now(timezone.utc).isoformat()
            })
        l3["patterns"] = patterns
        self._save_json(self.l3_path, l3)

    def recall(self, query: str, top_k: int = 5) -> List[dict]:
        l2 = self._load_json(self.l2_path)
        episodes = l2.get("episodes", [])
        associations = l2.get("associations", {})
        if not episodes: return []

        initial_scores = {}
        vector_success = False

        # 1. SemanticEngine을 사용한 벡터 유사도 검색
        if self.sem_engine:
            try:
                query_emb = self.sem_engine.get_embedding(query)
                if query_emb:
                    import numpy as np
                    for ep in episodes:
                        ep_id = ep["id"]
                        ep_emb = ep.get("embedding")
                        if not ep_emb:
                            ep_emb = self.sem_engine.get_embedding(ep["content"])
                            # ep["embedding"] = ep_emb  ← 재저장 금지: JSON 비대화 원인
                        if ep_emb:
                            # 코사인 유사도 계산
                            cos_sim = np.dot(query_emb, ep_emb) / (np.linalg.norm(query_emb) * np.linalg.norm(ep_emb))
                            initial_scores[ep_id] = max(0.0, (cos_sim + 1.0) / 2.0) * 10.0
                            # 임베딩을 JSON에 재저장하지 않음 (State Offloading: turbovec 사용)
                        else:
                            initial_scores[ep_id] = 0.0
                    vector_success = True
            except Exception as e:
                logger.error(f"❌ [Bio-Memory] 벡터 검색 오류: {e}")

        # 2. Fallback: 키워드 유사도 매칭
        if not vector_success:
            query_kws = set(ImportanceScorer.extract_keywords(query, max_kw=8))
            for ep in episodes:
                ep_id = ep["id"]
                kw_overlap = len(query_kws & set(ep.get("keywords", [])))
                content_match = sum(1 for kw in query_kws if kw.lower() in ep.get("content", "").lower())
                initial_scores[ep_id] = (kw_overlap * 3.0) + (content_match * 1.5)

        # 3. 에빙하우스 보유율 결합 (초기 점수 80% + 보유율 20%)
        for ep in episodes:
            ep_id = ep["id"]
            retention = ForgettingCurve.retention(ep.get("importance", 1.0), ep.get("last_accessed", ep.get("timestamp", "")))
            init_score = initial_scores.get(ep_id, 0.0)
            initial_scores[ep_id] = (init_score * 0.8) + (retention * 2.0)

        # 4. Spreading Activation (활성화 확산 알고리즘 - 1-Hop)
        propagated_scores = {ep_id: 0.0 for ep_id in initial_scores}
        decay_factor = 0.5
        for ep_id, score in initial_scores.items():
            if score > 2.0:  # 전파 임계치 돌파 시
                neighbors = associations.get(ep_id, [])
                for edge in neighbors:
                    target_id = edge["target"]
                    weight = edge.get("weight", 0.5)
                    if target_id in propagated_scores:
                        propagated_scores[target_id] += score * weight * decay_factor

        # 5. 최종 결합 점수 계산 및 랭킹 (초기 점수 70% + 전파 점수 30%)
        final_scores = []
        for ep in episodes:
            ep_id = ep["id"]
            total_score = (initial_scores.get(ep_id, 0.0) * 0.7) + (min(propagated_scores.get(ep_id, 0.0), 10.0) * 0.3)
            final_scores.append((total_score, ep))

        final_scores.sort(key=lambda x: x[0], reverse=True)
        results = []
        for _, ep in final_scores[:top_k]:
            ep["last_accessed"] = datetime.now(timezone.utc).isoformat()
            ep["access_count"] = ep.get("access_count", 0) + 1
            results.append(ep)
        self._save_json(self.l2_path, l2)
        return results

    def recall_text(self, query: str, top_k: int = 5) -> str:
        results = self.recall(query, top_k)
        if not results: return "🔍 저장소 내에 일치하는 에피소드 기억이 존재하지 않습니다."
        lines = [f"🧠 **하이브리드(NPU/맥락) 연상 복기 결과** (검색어: `{query}`)\n"]
        for i, ep in enumerate(results, 1):
            ts = ep.get("timestamp", "")[:16].replace("T", " ")
            retention = ForgettingCurve.retention(ep.get("importance", 1.0), ep.get("last_accessed", ep.get("timestamp", "")))
            lines.append(f"**{i}. [{ts}]** 점수: ⭐{ep.get('importance', 0):.1f} | 보유율: {retention:.0%}\n   `{ep.get('content', '')[:150]}...`\n")
        return "\n".join(lines)

    def _get_l2_bytes(self, episodes: list, associations: dict) -> int:
        """L2 payload의 실제 JSON 직렬화 크기를 계산 (파일 stat 대신)."""
        payload = {"episodes": episodes, "associations": associations, "version": "1.1"}
        return len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    def get_memory_status(self) -> str:
        l1 = self._load_json(self.l1_path) if self.l1_path.exists() else []
        l2 = self._load_json(self.l2_path)
        l3 = self._load_json(self.l3_path)
        episodes = l2.get("episodes", [])
        associations = l2.get("associations", {})
        l2_bytes = self._get_l2_bytes(episodes, associations)
        l2_max_mb = L2_MAX_BYTES / (1024 * 1024)
        forget_candidates = sum(1 for ep in episodes if ForgettingCurve.should_forget(ep.get("importance", 1.0), ep.get("last_accessed", ep.get("timestamp", ""))))
        edge_count = sum(len(targets) for targets in associations.values()) // 2
        procedural_count = len(l3.get("procedural", {}))
        return (
            "🧠 **Harness Bio-Memory 실시간 가동 현황**\\n\\n"
            f"• **L1 Working Memory**: `{len(l1)}/{L1_MAX_SIZE} items` (휘발성 대화 버퍼)\\n"
            f"• **L2 Episodic Cache**: `{len(episodes)}/{L2_MAX_SIZE} eps` / `{l2_bytes / 1024:.1f}KB` (1MB = {l2_max_mb:.0f}MB 한계)\\n"
            f"•   ↳ 연결 네트워크 엣지: `{edge_count}개 연상 고리` 구축 완료\\n"
            f"•   ↳ 현재 소멸 직전 상태(보유율 20% 미만): `{forget_candidates}개` 항목\\n"
            f"• **L3 Semantic Core**: `{len(l3.get('patterns', []))} 규칙` 및 `{procedural_count} 절차 패턴` 확립 (영구 지식화 지표)"
        )

    def get_repeated_patterns(self, threshold: int = 3) -> List[Dict]:
        """L3 패턴 중 frequency >= threshold 인 항목 반환.

        Args:
            threshold: 최소 반복 횟수 (기본: 3, style_profile.md 규칙)

        Returns:
            frequency >= threshold 인 패턴 리스트
        """
        l3 = self._load_json(self.l3_path)
        patterns = l3.get("patterns", [])
        repeated = [p for p in patterns if p.get("frequency", 0) >= threshold]
        return repeated

    def save_procedural_memory(self, action_name: str, command_sequence: List[str], success: bool = True):
        """행동 패턴 및 성공한 명령어 흐름을 L3 절차 기억으로 저장합니다."""
        l3 = self._load_json(self.l3_path)
        procedural = l3.get("procedural", {})
        keywords = ImportanceScorer.extract_keywords(action_name)
        proc_id = f"proc_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        procedural[action_name] = {
            "id": proc_id,
            "commands": command_sequence,
            "keywords": keywords,
            "success": success,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "use_count": 0
        }
        l3["procedural"] = procedural
        self._save_json(self.l3_path, l3)
        logger.info(f"⚙️ [Bio-Memory] 절차 기억 등록 완료: '{action_name}' ({len(command_sequence)} 명령어)")

    def recall_procedural_memory(self, action_query: str) -> Optional[Dict]:
        """주어진 행동 질문에 부합하는 절차 기억(성공 사례)을 인출합니다."""
        l3 = self._load_json(self.l3_path)
        procedural = l3.get("procedural", {})
        if not procedural: return None
        query_kws = set(ImportanceScorer.extract_keywords(action_query))
        best_match = None
        max_score = 0.0
        for action_name, proc in procedural.items():
            action_kws = set(proc.get("keywords", []))
            overlap = len(query_kws & action_kws)
            name_score = sum(3.0 for kw in query_kws if kw.lower() in action_name.lower())
            score = (overlap * 2.0) + name_score
            if score > max_score and proc.get("success", True):
                max_score = score
                best_match = proc
                best_match["action_name"] = action_name
        if best_match and max_score >= 2.0:
            best_match["use_count"] = best_match.get("use_count", 0) + 1
            l3["procedural"][best_match["action_name"]]["use_count"] = best_match["use_count"]
            self._save_json(self.l3_path, l3)
            return best_match
        return None

    def save_important(self, key: str, val: str) -> str:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.write(f"- [{timestamp}] [{key}] {val}\n")
            self._promote_to_l2({"role": "system", "content": f"[{key}] {val}", "timestamp": datetime.now(timezone.utc).isoformat(), "importance": 8.0})
            return f"✅ 핵심 영구 장부(memory.md) 및 L2/L3 계층 동시 인코딩 완료."
        except Exception as e:
            return f"❌ 기억 저장 중 물리적 디스크 에러: {e}"

    async def dream(self, history_data: list, llm_func=None) -> str:
        try:
            report = self._offline_consolidation(history_data or [])
            if not llm_func: return f"❌ LLM 바인딩 미전달. 단기 기억 압축만 수행.\n{report}"
            today_str = datetime.now().strftime("%Y.%m.%d")
            hot_content = ""
            if os.path.exists(self.hot_file):
                with open(self.hot_file, "r", encoding="utf-8") as f:
                    hot_content = f.read()
            chat_text = "\n".join([f"{h['role']}: {h['content'][:150]}" for h in history_data[-10:]])
            sys_prompt = "당신은 비서 '하네스'의 지식 정리 엔진입니다. JSON 형식 규칙을 준수하여 마크다운 블록 없이 반환하세요."
            prompt = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": f"[대화]\n{chat_text}\n\n[hot]\n{hot_content}"}]
            llm_response, _ = await llm_func(prompt)
            clean_res = llm_response.strip().replace("```json", "").replace("```", "")
            parsed = json.loads(clean_res)
            
            # hot.md 업데이트 시 원자적 덮어쓰기 사용
            temp_hot_path = Path(self.hot_file).with_suffix('.tmp')
            with open(temp_hot_path, "w", encoding="utf-8") as f:
                f.write(f"# 📝 프로젝트 핫토픽 (Dreaming 최신화)\n\n## 📌 현재 진행 중인 작업\n")
                for r in parsed.get("hot_remains", []): f.write(f"- {r}\n")
            temp_hot_path.replace(self.hot_file)
            
            return f"🌙 **지능형 3계층 Dreaming 세션 완료 ({today_str})**\n\n{report}"
        except Exception as e:
            return f"❌ Dreaming 정리 스케줄러 장애: {e}"

    def _offline_consolidation(self, history_data: list) -> str:
        report = []
        l2 = self._load_json(self.l2_path)
        episodes = l2.get("episodes", [])
        kept, forgotten = [], 0
        for ep in episodes:
            if ForgettingCurve.should_forget(ep.get("importance", 1.0), ep.get("last_accessed", ep.get("timestamp", ""))):
                self._note_l3_candidate(ep)
                forgotten += 1
            else: kept.append(ep)
        l2["episodes"] = kept
        self._save_json(self.l2_path, l2)
        if forgotten: report.append(f"  · 에빙하우스 망각선 미달 기억 인지 압축 완료: {forgotten}건")
        return "\n".join(report) if report else "  · 대화 버퍼 신선도 양호 및 인지 대칭성 유지 중."

    def pre_query_context(self, query: str) -> Optional[str]:
        """
        L2/L3 사전 질의 — "모르겠음" 전에 모든 기억 계층 검색.
        L2 episodic + L3 semantic patterns + L3 procedural 통합.
        1시간 캐싱으로 반복 질문 속도 향상.

        Args:
            query: 사용자의 원본 질문

        Returns:
            통합 기억 컨텍스트 문자열 (없으면 None)
        """
        query_key = query.strip().lower()[:200]
        cache_hit = _query_cache.get(query_key)
        if cache_hit and (time.time() - cache_hit[0]) < QUERY_CACHE_TTL:
            logger.debug(f"🔁 [Bio-Memory] 캐시 히트: {query[:50]}...")
            return cache_hit[1]

        parts = []

        # 1. L2 episodic recall
        l2_results = self.recall(query, top_k=3)
        if l2_results:
            parts.append("🧠 [L2 연상 기억]")
            for ep in l2_results:
                ts = ep.get("timestamp", "")[:16].replace("T", " ")
                imp = ep.get("importance", 0)
                parts.append(f"  · [{ts}] ⭐{imp:.1f} — {ep.get('content', '')[:200]}")

        # 2. L3 semantic patterns (키워드 교차 매칭)
        l3 = self._load_json(self.l3_path)
        patterns = l3.get("patterns", [])
        query_kws = set(ImportanceScorer.extract_keywords(query, max_kw=8))
        matched_patterns = []
        for pat in patterns:
            pat_kws = set(pat.get("keywords", []))
            if len(query_kws & pat_kws) >= 2:
                matched_patterns.append(pat)
        if matched_patterns:
            parts.append("📐 [L3 패턴 기억]")
            for pat in matched_patterns[:3]:
                freq = pat.get("frequency", 1)
                parts.append(f"  · {pat.get('summary', '')[:200]} (빈도: {freq})")

        # 3. L3 procedural memory (키워드 + 액션명 매칭)
        procedural = l3.get("procedural", {})
        matched_procs = []
        for action_name, proc in procedural.items():
            proc_kws = set(proc.get("keywords", []))
            if len(query_kws & proc_kws) >= 1:
                matched_procs.append((action_name, proc))
            else:
                # 액션명에 쿼리 키워드가 하나라도 포함되면 매치
                for kw in query_kws:
                    if kw.lower() in action_name.lower():
                        matched_procs.append((action_name, proc))
                        break
        if matched_procs:
            parts.append("⚙️ [L3 절차 기억]")
            for name, proc in matched_procs[:3]:
                cmds = len(proc.get("commands", []))
                success = "✅" if proc.get("success", True) else "❌"
                parts.append(f"  · {success} {name}: {cmds}단계 절차")

        result = "\n".join(parts) if parts else None

        # 캐시 저장
        _query_cache[query_key] = (time.time(), result)
        # 캐시 사이즈 관리 (max 200 entries)
        if len(_query_cache) > 200:
            old_keys = sorted(_query_cache, key=lambda k: _query_cache[k][0])[:50]
            for k in old_keys:
                del _query_cache[k]

        return result

    def get_enriched_context(self, current_query: str, max_history: int = 10) -> list:
        history = []
        if self.l1_path.exists():
            try:
                l1 = json.loads(self.l1_path.read_text(encoding="utf-8"))
                history = [{"role": m["role"], "content": m["content"]} for m in l1[-max_history:]]
            except: pass
        recalled = self.recall(current_query, top_k=3)

        # L3 pre-query 통합
        l3_context = self.pre_query_context(current_query)

        memory_parts = []
        l2_found = bool(recalled)

        if recalled:
            memory_parts.append("⚠️ [관련 과거 기억 오버레이 가동]\n당신은 박사님과 나눈 과거 대화 중 다음의 맥락을 연상해내어 인지하고 있는 상태입니다:")
            for ep in recalled:
                memory_parts.append(f"- {ep.get('content')[:250]}")

        # L3 결과는 L2가 없을 때만 별도 표시 (L2가 이미 덮고 있으면 중복 방지)
        if l3_context and not l2_found:
            memory_parts.append(f"⚠️ [심층 기억 연상 결과]\n{l3_context}")

        if memory_parts:
            return [{"role": "system", "content": "\n".join(memory_parts)}] + history

        return history


# ── turbovec 경량 벡터 인덱스 옵션 ─────────────────────────
# Parallel/supplemental vector index alongside SemanticEngine (NPU BGE-M3).
# Configurable backend: "npu" (default), "turbovec", or "hybrid" (both -> rank fusion).

import numpy as np

# 외부 turbovec 모듈 시도, 실패 시 내장 간이 구현
try:
    from turbovec import TurboVecIndex
    _HAS_TURBOVEC = True
except ImportError:
    _HAS_TURBOVEC = False

VECTOR_BACKEND = "hybrid"  # "npu" | "turbovec" | "hybrid"
VECTOR_BACKEND_CONFIG_PATH = Path("/Users/bluesea/.hermes/memory/vector_backend.json")


class TurboVecLight:
    """turbovec 간이 구현 — 내장 numpy 기반 경량 인덱스.

    NPU SemanticEngine 병렬/대체 옵션.
    """

    def __init__(self, dim: int = 384, index_path: Path = None):
        self.dim = dim
        self.index_path = index_path or Path("/Users/bluesea/.hermes/memory/turbovec_index.npy")
        self.keys_path = index_path.with_suffix(".keys.json") if index_path else Path("/Users/bluesea/.hermes/memory/turbovec_keys.json")
        self.vectors: np.ndarray = np.empty((0, dim), dtype=np.float32)
        self.keys: list = []
        self._load()

    def _load(self):
        if self.index_path.exists():
            try:
                self.vectors = np.load(str(self.index_path))
            except Exception:
                self.vectors = np.empty((0, self.dim), dtype=np.float32)
        if self.keys_path.exists():
            try:
                self.keys = json.loads(self.keys_path.read_text(encoding="utf-8"))
            except Exception:
                self.keys = []

    def _save(self):
        np.save(str(self.index_path), self.vectors)
        self.keys_path.write_text(json.dumps(self.keys, ensure_ascii=False), encoding="utf-8")

    def add(self, key: str, vector: list):
        """벡터와 키 추가."""
        vec = np.array(vector, dtype=np.float32).reshape(1, -1)
        existing = [i for i, k in enumerate(self.keys) if k == key]
        if existing:
            idx = existing[0]
            self.vectors[idx] = vec
        else:
            self.vectors = np.vstack([self.vectors, vec]) if self.vectors.size else vec
            self.keys.append(key)
        self._save()

    def search(self, query_vec: list, top_k: int = 5) -> list:
        """코사인 유사도 기반 검색.

        Args:
            query_vec: 질의 임베딩 벡터
            top_k: 상위 N개

        Returns:
            [(key: str, score: float), ...]
        """
        if self.vectors.shape[0] == 0:
            return []
        qv = np.array(query_vec, dtype=np.float32).reshape(1, -1)
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        q_norm = np.linalg.norm(qv)
        if q_norm == 0:
            return []
        sims = (self.vectors @ qv.T).flatten() / (norms.flatten() * q_norm + 1e-8)
        sims = np.nan_to_num(sims, nan=0.0)
        top_idx = np.argsort(sims)[::-1][:top_k]
        results = []
        for i in top_idx:
            if i < len(self.keys):
                results.append((self.keys[i], float(sims[i])))
        return results

    def remove(self, key: str):
        """키로 항목 삭제."""
        idx = [i for i, k in enumerate(self.keys) if k == key]
        if idx:
            i = idx[0]
            self.keys.pop(i)
            self.vectors = np.delete(self.vectors, i, axis=0)
            self._save()

    def size(self) -> int:
        return len(self.keys)


# ── 벡터 백엔드 설정 관리 ──────────────────────────────────
def get_vector_backend() -> str:
    """현재 설정된 벡터 백엔드 반환."""
    if VECTOR_BACKEND_CONFIG_PATH.exists():
        try:
            cfg = json.loads(VECTOR_BACKEND_CONFIG_PATH.read_text(encoding="utf-8"))
            return cfg.get("backend", VECTOR_BACKEND)
        except Exception:
            pass
    return VECTOR_BACKEND


def set_vector_backend(backend: str):
    """벡터 백엔드 전환. 'npu' | 'turbovec' | 'hybrid'"""
    global VECTOR_BACKEND
    if backend not in ("npu", "turbovec", "hybrid"):
        logger.warning(f"⚠️ 지원하지 않는 벡터 백엔드: {backend} (유지: {VECTOR_BACKEND})")
        return
    VECTOR_BACKEND = backend
    VECTOR_BACKEND_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    VECTOR_BACKEND_CONFIG_PATH.write_text(
        json.dumps({"backend": backend, "updated_at": datetime.now().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"⚡ [turbovec] 벡터 백엔드 전환: {backend}")


class VectorIndexManager:
    """통합 벡터 인덱스 매니저 — NPU / turbovec / hybrid 통합.

    GPU/CPU/온도에 따른 동적 백엔드 전환 지원.
    """

    def __init__(self, sem_engine=None, dim: int = 384):
        self.sem_engine = sem_engine
        self.dim = dim
        self.turbovec = TurboVecLight(dim=dim)

        # Allowlist: 그래프 후보 필터 (이 키워드가 포함된 항목만 그래프 연결)
        self.allowlist = {
            "에러", "버그", "오류", "수정", "복구", "긴급",
            "설정", "경로", "포트", "토큰", "API", "PATH",
            "승인", "결정", "규칙", "기억", "중요",
            "확정", "완료", "배포", "실패", "치명",
            "auto-skill", "Curator", "Honcho", "Dreaming", "turbovec",
        }

    def get_embedding(self, text: str) -> Optional[list]:
        """백엔드 설정에 따라 임베딩 생성."""
        backend = get_vector_backend()

        npu_vec = None
        if backend in ("npu", "hybrid") and self.sem_engine:
            try:
                npu_vec = self.sem_engine.get_embedding(text)
            except Exception as e:
                logger.warning(f"⚠️ [VectorIndex] NPU 임베딩 실패: {e}")

        if backend == "turbovec":
            # turbovec 단독 모드: 별도 임베딩 모델 필요
            return None  # fallback to hybrid

        return npu_vec

    def add_to_index(self, key: str, text: str):
        """양 백엔드에 동시 등록."""
        backend = get_vector_backend()

        if backend in ("turbovec", "hybrid"):
            if self.sem_engine:
                try:
                    vec = self.sem_engine.get_embedding(text)
                    if vec:
                        self.turbovec.add(key, vec)
                except Exception:
                    pass

        if backend in ("npu", "hybrid"):
            pass  # NPU는 SemanticEngine이 직접 관리

    def search(self, query: str, top_k: int = 5) -> list:
        """통합 검색: 설정된 백엔드에 따라 검색 후 rank fusion.

        Returns:
            [(key, score), ...] (allowlist 필터링 포함)
        """
        backend = get_vector_backend()
        if not self.sem_engine:
            logger.warning("⚠️ [VectorIndex] SemanticEngine 없음, 키워드 검색 fallback")
            return []

        query_vec = self.sem_engine.get_embedding(query)
        if not query_vec:
            return []

        results = []

        if backend in ("npu", "hybrid"):
            # NPU 검색: L2 episodic 내 임베딩 유사도
            pass  # 호출 측에서 처리

        if backend in ("turbovec", "hybrid"):
            tv_results = self.turbovec.search(query_vec, top_k=top_k * 2)
            results.extend(tv_results)

        # allowlist 필터링: 그래프 연결 후보는 allowlist 키워드 포함 항목만
        filtered = []
        for key, score in results:
            # 키 또는 내용에 allowlist 키워드 포함 여부
            if any(kw in key.lower() for kw in self.allowlist):
                filtered.append((key, score))
            else:
                filtered.append((key, score * 0.3))  # 비허용 리스트는 감점

        # Sort by score descending, take top_k
        filtered.sort(key=lambda x: x[1], reverse=True)
        return filtered[:top_k]

    def is_allowlisted(self, key: str) -> bool:
        """그래프 후보 자격 확인."""
        return any(kw in key.lower() for kw in self.allowlist)


# ── BioMemoryEngine.vim 자동 바인딩 (모듈 로드 완료 후 실행) ────────────
# VectorIndexManager가 BioMemoryEngine보다 나중에 정의되므로 모듈 레벨에서 패치
def _auto_bind_vim(engine: "BioMemoryEngine"):
    """BioMemoryEngine 인스턴스에 VectorIndexManager를 자동 바인딩.

    __init__에서 vim=None으로 초기화 후, 모듈 완전 로드 이후 이 함수를 통해
    VectorIndexManager를 연결. harness_agent.py의 LazyService 패턴과 호환.
    """
    if engine.sem_engine and engine.vim is None:
        try:
            engine.vim = VectorIndexManager(sem_engine=engine.sem_engine)
            logger.info(f"⚡ [Bio-Memory] VectorIndexManager 자동 바인딩 완료")
            # 이미 인라인 임베딩이 있으면 turbovec으로 이관
            engine._strip_inline_embeddings()
        except Exception as e:
            logger.warning(f"[Bio-Memory] vim 자동 바인딩 실패: {e}")


# ── BioMemoryEngine 확장 메서드 ─────────────────────────────
def init_vector_index(engine: BioMemoryEngine):
    """BioMemoryEngine에 VectorIndexManager 연결.

    기존 sem_engine를 공유하며 turbovec 병렬 운용.

    Usage:
        vim = init_vector_index(bio_engine)
        # 이후 bio_engine.vim 으로 접근
    """
    if engine.sem_engine:
        vim = VectorIndexManager(sem_engine=engine.sem_engine)
        engine.vim = vim
        backend = get_vector_backend()
        logger.info(f"⚡ [VectorIndex] 초기화 완료 (백엔드: {backend}, allowlist={len(vim.allowlist)}개)")
        return vim
    logger.warning("⚠️ [VectorIndex] SemanticEngine 없음 — VectorIndex 미초기화")
    return None