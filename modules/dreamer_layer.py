"""
Harness V2.5 — Dreamer Layer (L3 통합/꿈꾸기 엔진)
====================================================
Life-Harness Layer 3: L2→L3 의미 기억 통합 + 오프라인 정리

주요 기능:
- L3 의미 기억 패턴 통합 (Pattern Merging)
- 절차 기억 관리 (Procedural Memory)
- LLM 기반 지능형 Dreaming 파이프라인
- 에빙하우스 망각 곡선 기반 오프라인 정리 (Consolidation)
- hot.md Dreaming 업데이트
- 메모리 상태 보고서

arXiv 2605.22166 — Memory Dreaming Architecture
"""

import json
import os
import math
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger("DreamerLayer")

# ── 기본 상수 ──────────────────────────────────────────────
DEFAULT_VAULT = "/Users/bluesea/Applications/Mjobsidian"
MEMORY_DIR = Path("/Users/bluesea/.hermes/memory")


# ── JSON 유틸리티 ──────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"patterns": []}


def _save_json(path: Path, data: dict):
    """원자적(Atomic) 파일 저장."""
    temp_path = path.with_suffix('.tmp')
    try:
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    except Exception as e:
        logger.error(f"❌ [Dreamer] 원자적 파일 저장 실패 ({path.name}): {e}")
        if temp_path.exists():
            temp_path.unlink()


# ── Forgetting Curve (에빙하우스 재사용) ──────────────────

def _retention(importance: float, last_accessed_iso: str) -> float:
    try:
        last = datetime.fromisoformat(last_accessed_iso)
    except Exception:
        last = datetime.now(timezone.utc)
    days_elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 86400
    stability = importance * 5.0
    return round(math.exp(-days_elapsed / max(stability, 0.1)), 4)


def _should_forget(importance: float, last_accessed_iso: str) -> bool:
    if importance >= 2.0:
        return False
    try:
        last = datetime.fromisoformat(last_accessed_iso)
    except Exception:
        return False
    days_elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 86400
    return days_elapsed >= 14


# ── Dreamer Engine ─────────────────────────────────────────

class DreamerEngine:
    """
    L2→L3 의미 기억 통합 + 절차 기억 + Dreaming 파이프라인.

    Dreamer는 배치/오프라인 작업을 담당하여,
    L2에서 밀려난 에피소드를 L3 의미 패턴으로 통합하고,
    정기적 Dreaming 사이클을 통해 지식을 압축/정리합니다.
    """

    def __init__(self, vault_path: str = DEFAULT_VAULT):
        self.vault_path = Path(vault_path)
        self.meta_dir = self.vault_path / "wiki" / "00_Meta"
        self.hot_file = str(self.meta_dir / "hot.md")
        self.style_profile_file = str(self.meta_dir / "style_profile.md")
        self.l3_path = MEMORY_DIR / "semantic_memory.json"
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_structures()

    def _ensure_structures(self):
        """L3 JSON 구조 초기화."""
        if not self.l3_path.exists():
            _save_json(self.l3_path, {
                "version": "1.1",
                "patterns": [],
                "user_model": {
                    "preferences": [],
                    "frequent_topics": {},
                    "system_rules": [],
                },
                "procedural": {},
            })
        else:
            l3 = _load_json(self.l3_path)
            if "procedural" not in l3:
                l3["procedural"] = {}
                l3["version"] = "1.1"
                _save_json(self.l3_path, l3)

    # ═══════════════════════════════════════════════════════
    # L3 패턴 통합
    # ═══════════════════════════════════════════════════════

    def note_l3_candidate(self, episode: dict):
        """L2에서 밀려난 에피소드를 L3 의미 패턴으로 통합/병합."""
        l3 = _load_json(self.l3_path)
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
        _save_json(self.l3_path, l3)

    def batch_note_l3(self, episodes: list):
        """여러 에피소드를 한 번에 L3로 통합."""
        for ep in episodes:
            self.note_l3_candidate(ep)

    # ═══════════════════════════════════════════════════════
    # 절차 기억 (Procedural Memory)
    # ═══════════════════════════════════════════════════════

    def save_procedural(self, action_name: str, command_sequence: List[str], success: bool = True):
        """성공한 명령어 흐름을 L3 절차 기억으로 저장."""
        l3 = _load_json(self.l3_path)
        procedural = l3.get("procedural", {})

        proc_id = f"proc_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        # 키워드 추출 (Deriver Layer 의존 최소화)
        import re
        keywords = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", action_name)[:5]

        procedural[action_name] = {
            "id": proc_id,
            "commands": command_sequence,
            "keywords": keywords,
            "success": success,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "use_count": 0,
        }
        l3["procedural"] = procedural
        _save_json(self.l3_path, l3)
        logger.info(f"⚙️ [Dreamer] 절차 기억 등록: '{action_name}' ({len(command_sequence)} 명령어)")

    def recall_procedural(self, action_query: str) -> Optional[Dict]:
        """질문과 일치하는 절차 기억(성공 사례) 인출."""
        l3 = _load_json(self.l3_path)
        procedural = l3.get("procedural", {})
        if not procedural:
            return None

        import re
        query_kws = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", action_query))
        if not query_kws:
            return None

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
            proc_name = best_match["action_name"]
            l3["procedural"][proc_name]["use_count"] = best_match["use_count"]
            _save_json(self.l3_path, l3)
            return best_match

        return None

    # ═══════════════════════════════════════════════════════
    # 오프라인 통합 (Offline Consolidation)
    # ═══════════════════════════════════════════════════════

    def offline_consolidation(self, history_data: list) -> str:
        """
        L2 에피소드 정리: (1) 망각 곡선 정리 (2) 용량 기반 증류 (3) L3 승격.
        Dreaming 사이클의 일부로 호출됨.
        """
        l2_path = MEMORY_DIR / "episodic_memory.json"
        if not l2_path.exists():
            return "  · L2 저장소 없음. 통합 생략."

        l2 = _load_json(l2_path)
        episodes = l2.get("episodes", [])
        kept, forgotten, distilled = [], 0, 0

        # L2 파일 크기 확인
        l2_bytes = l2_path.stat().st_size if l2_path.exists() else 0
        L2_MAX_BYTES = 1 * 1024 * 1024  # 1MB

        for ep in episodes:
            content = ep.get("content", "")
            ep_len = len(content)
            importance = ep.get("importance", 1.0)

            # 조건 A: 에빙하우스 망각선 도달 → L3 전이 후 제거
            if _should_forget(importance, ep.get("last_accessed", ep.get("timestamp", ""))):
                self.note_l3_candidate(ep)
                forgotten += 1
                continue

            # 조건 B: 용량 기반 증류 — L2가 1MB 초과 시 긴/중요한 에피소드도 L3로
            if l2_bytes > L2_MAX_BYTES:
                if ep_len > 500 and importance >= 4.0:
                    # 내용이 긴 고중요도 항목: L3에 요약 저장 후 L2에서 제거
                    self.note_l3_candidate(ep)
                    distilled += 1
                    continue
                elif ep_len > 800:
                    # 내용이 매우 긴 항목: 중요도와 무관하게 L3 요약 저장 후 제거
                    self.note_l3_candidate(ep)
                    distilled += 1
                    continue

            kept.append(ep)

        l2["episodes"] = kept
        _save_json(l2_path, l2)

        parts = []
        if forgotten:
            parts.append(f"망각 정리 {forgotten}건")
        if distilled:
            parts.append(f"증류 전환 {distilled}건")
        if not parts:
            return "  · 대화 버퍼 신선도 양호 및 인지 대칭성 유지 중."
        return f"  · {' + '.join(parts)} — L2 {len(kept)}개 유지, L3 패턴에 통합 완료."

    def offline_consolidation_forced(self) -> str:
        """
        조건 무관 강제 증류: L2 에피소드 중 중요도 하위 50%를 L3로 전이 후 제거.
        L2 용량 위기 시 Emergency 호출용.
        """
        l2_path = MEMORY_DIR / "episodic_memory.json"
        if not l2_path.exists():
            return "  · L2 저장소 없음. 강제 통합 생략."

        l2 = _load_json(l2_path)
        episodes = l2.get("episodes", [])
        if not episodes:
            return "  · L2 비어 있음."

        # 중요도 기준 정렬 후 하위 50% L3 전이
        episodes.sort(key=lambda x: x.get("importance", 1.0))
        mid = max(1, len(episodes) // 2)
        to_distill = episodes[:mid]
        kept = episodes[mid:]

        for ep in to_distill:
            self.note_l3_candidate(ep)

        l2["episodes"] = kept
        _save_json(l2_path, l2)

        return f"  · [Emergency] 강제 증류 완료: {len(to_distill)}건 → L3 전이, L2 {len(kept)}개 유지."

    # ═══════════════════════════════════════════════════════
    # Style Profile 연동
    # ═══════════════════════════════════════════════════════

    def _read_style_profile(self) -> str:
        """style_profile.md를 읽어 스타일 가이드 반환."""
        if os.path.exists(self.style_profile_file):
            try:
                with open(self.style_profile_file, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                logger.warning(f"style_profile 읽기 실패: {e}")
        return ""

    def _write_style_profile(self, content: str):
        """style_profile.md를 원자적으로 갱신."""
        try:
            temp_path = Path(self.style_profile_file).with_suffix('.tmp')
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(self.style_profile_file)
            logger.info(f"🎭 Style Profile 갱신 완료 ({len(content)} chars)")
        except Exception as e:
            logger.error(f"style_profile 갱신 실패: {e}")

    # ═══════════════════════════════════════════════════════
    # Dreaming Pipeline (LLM 기반)
    # ═══════════════════════════════════════════════════════

    async def dream(self, history_data: list, llm_func=None) -> str:
        """
        LLM 기반 지능형 Dreaming 실행:
        1. 오프라인 통합 (L2 정리)
        2. LLM에 hot.md + 최근 대화 전달
        3. hot.md 업데이트
        """
        try:
            report = self.offline_consolidation(history_data or [])
            if not llm_func:
                return f"❌ LLM 바인딩 미전달. 단기 기억 압축만 수행.\n{report}"

            today_str = datetime.now().strftime("%Y.%m.%d")
            hot_content = ""
            if os.path.exists(self.hot_file):
                with open(self.hot_file, "r", encoding="utf-8") as f:
                    hot_content = f.read()

            # 최근 대화 요약
            chat_text = "\n".join(
                f"{h['role']}: {h['content'][:150]}"
                for h in (history_data or [])[-10:]
            )

            sys_prompt = "당신은 비서 '하네스'의 지식 정리 엔진입니다. JSON 형식 규칙을 준수하여 마크다운 블록 없이 반환하세요."

            # style_profile 주입
            style_guide = self._read_style_profile()
            style_section = ""
            if style_guide:
                style_section = f"\n\n[출력 스타일 가이드]\n{style_guide[:2000]}"

            prompt = [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": f"[대화]\n{chat_text}\n\n[hot]\n{hot_content}{style_section}",
                },
            ]

            llm_response, _ = await llm_func(prompt)
            clean_res = llm_response.strip().replace("```json", "").replace("```", "")
            parsed = json.loads(clean_res)

            # hot.md 원자적 업데이트
            temp_hot = Path(self.hot_file).with_suffix('.tmp')
            with open(temp_hot, "w", encoding="utf-8") as f:
                f.write("# 🗂️ 프로젝트 핫토픽 (Dreaming 최신화)\n\n")
                f.write("## 📌 현재 진행 중인 작업\n")
                for r in parsed.get("hot_remains", []):
                    f.write(f"- {r}\n")
            temp_hot.replace(self.hot_file)

            # style_profile 동기화 (Dreaming 사이클 종료 시)
            try:
                from modules.dialectic_layer import sync_style_profile
                sync_style_profile()
            except Exception:
                pass

            return f"🌙 **지능형 3계층 Dreaming 세션 완료 ({today_str})**\n\n{report}"

        except Exception as e:
            return f"❌ Dreaming 정리 스케줄러 장애: {e}"

    # ═══════════════════════════════════════════════════════
    # 메모리 상태
    # ═══════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """Dreamer Layer 상태 정보 반환."""
        l3 = _load_json(self.l3_path)
        patterns = l3.get("patterns", [])
        procedural = l3.get("procedural", {})
        user_model = l3.get("user_model", {})
        return {
            "l3_patterns": len(patterns),
            "procedural_count": len(procedural),
            "user_preferences": len(user_model.get("preferences", [])),
            "frequent_topics": len(user_model.get("frequent_topics", {})),
            "system_rules": len(user_model.get("system_rules", [])),
        }

    def get_status_text(self) -> str:
        """Dreamer Layer 상태 텍스트 반환."""
        s = self.get_status()
        return (
            f"• **L3 Semantic Core**: `{s['l3_patterns']} 규칙` 및 "
            f"`{s['procedural_count']} 절차 패턴` 확립 (영구 지식화 지표)"
        )
