"""
Harness V2.5 — Skill Evolver (v2: Procedural Skill Layer)
===========================================================
- 에러 복구 성공 시 SKILL.md 자동 생성/패치
- 대화 기반 학습 (learning interaction) 트리거 추가
- 스킬 카테고리 분류 및 통계
- Vault 동기화 (충돌 해결 포함)
- 오래된 스킬 정리 (Pruning)

원본: module2_skill_evolver.py → v2 고도화 (2026-05-24)
"""

import json
import hashlib
import logging
import shutil
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

# ── 경로 설정 ──────────────────────────────────────────────
SKILLS_ROOT  = Path("/Users/bluesea/.hermes/skills/learned")
VAULT_SKILLS = Path("/Users/bluesea/Applications/Mjobsidian/wiki/10_AI_Automation/skills")
SESSION_LOG  = Path("/Users/bluesea/.hermes/session_events.jsonl")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SkillEvolver] %(message)s")
log = logging.getLogger("skill_evolver")


# ── SKILL.md 템플릿 ────────────────────────────────────────
SKILL_MD_TEMPLATE = """\
# SKILL: {skill_name}

## 메타데이터
- **생성일**: {timestamp}
- **트리거**: {trigger}
- **카테고리**: {category}
- **원본 명령어**: `{original_cmd}`
- **실패 원인**: {error_msg}
- **복구 명령어**: `{fixed_cmd}`
- **재시도 횟수**: {retries}회
- **사용 횟수**: 1

## 문제 설명
{problem}

## 해결 방법
{solution}

## 재사용 가이드
유사한 에러 상황에서 아래 절차를 따르십시오:

1. 에러 키워드 매칭: `{keywords}`
2. 복구 명령어 패턴 적용
3. 결과 검증 후 완료

## 관련 스킬
{related}

---
*Harness V2.5 Self-Evolving Skill — {trigger} 이벤트로 자동 생성*
"""

LEARNING_SKILL_TEMPLATE = """\
# SKILL: {skill_name}

## 메타데이터
- **생성일**: {timestamp}
- **트리거**: learning_interaction
- **카테고리**: {category}
- **원본 질문**: {question}
- **핵심 개념**: {concepts}

## 학습 내용
{summary}

## 적용 분야
{application}

## 참고
{references}

---
*Harness V2.5 Self-Evolving Skill — 대화 기반 자동 학습*
"""


# ── 카테고리 분류 ──────────────────────────────────────────
CATEGORIES = {
    "file_ops":      ["file", "move", "copy", "create", "read", "write", "ls", "mv", "cp", "rm", "mkdir", "chmod"],
    "network":       ["curl", "wget", "ping", "ssh", "scp", "http", "api", "download", "git clone", "npm install"],
    "python":        ["python", "pip", "conda", "import", "module", "virtualenv", "pytest", "unittest"],
    "system":        ["brew", "launchctl", "system", "process", "kill", "ps", "top", "df", "du", "diskutil"],
    "docker":        ["docker", "container", "image", "compose", "dockerfile"],
    "llm":           ["llama", "gguf", "ollama", "model", "token", "inference", "quantization"],
    "obsidian":      ["obsidian", "vault", "markdown", "wiki", "note"],
    "telegram":      ["telegram", "bot", "message", "chat", "webhook"],
    "git":           ["git", "commit", "push", "pull", "branch", "merge", "pr", "github"],
}


def _classify(cmd: str) -> str:
    """명령어를 카테고리로 분류."""
    cmd_lower = cmd.lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw in cmd_lower for kw in keywords):
            return cat
    return "general"


def _skill_id(content: str) -> str:
    return hashlib.md5(content[:80].encode()).hexdigest()[:8]


def _skill_name(problem: str) -> str:
    slug = problem[:40].lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = slug.strip().replace(" ", "_")
    return f"{slug}_{_skill_id(problem)}"


def _find_related(skill_name: str, category: str, limit: int = 3) -> list:
    """같은 카테고리의 기존 스킬 검색 (자기 자신 제외)."""
    if not SKILLS_ROOT.exists():
        return []
    results = []
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name == skill_name:
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8")
                if f"**카테고리**: {category}" in content:
                    # Extract skill name from heading
                    for line in content.split("\n"):
                        if line.startswith("# SKILL:"):
                            results.append(line.replace("# SKILL:", "").strip())
                            break
            except Exception:
                continue
    return results[:limit]


# ── 메인 API ────────────────────────────────────────────────

class SkillEvolver:
    """Dreaming 사이클에서 호출되는 Skill 자동 생성기.

    L3 반복 패턴을 받아 신규 SKILL.md를 생성합니다.

    SkillOpt 경량화 (v2.5):
    - 편집예산: 기본 4회, cosine decay
    - 홀드아웃: 신규 스킬이 기존 최고보다 효과적인 경우만 승인
    - 거절 버퍼: 부정 피드백 추적
    """

    # SkillOpt 파라미터
    DEFAULT_EDIT_BUDGET = 4  # 스킬당 최대 편집 횟수
    COSINE_CYCLE = 7  # 7회 cosine decay로 budget 0

    def __init__(self):
        SKILLS_ROOT.mkdir(parents=True, exist_ok=True)

    def _edit_budget(self, skill_name: str) -> int:
        """해당 스킬의 남은 편집 예산 계산 (cosine decay)."""
        if not skill_name:
            return self.DEFAULT_EDIT_BUDGET

        # 기존 편집 횟수 확인
        skill_dir = SKILLS_ROOT / skill_name
        edit_count = 0
        if skill_dir.exists():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                edit_count = content.count("## 패치 [")
                # 거절 버퍼에서도 감소
        # cosine decay: budget * (1 - cos(n / cycle * pi/2))
        import math
        remaining = max(0, int(self.DEFAULT_EDIT_BUDGET * (1 - math.cos(min(edit_count, self.COSINE_CYCLE) / self.COSINE_CYCLE * math.pi / 2))))
        log.debug(f"📊 편집예산: {skill_name} → {remaining}/{self.DEFAULT_EDIT_BUDGET} (편집횟수={edit_count})")
        return remaining

    def _holdout_validate(self, existing_content: str, new_content: str) -> bool:
        """홀드아웃 검증: 신규 내용이 기존 최고보다 효과적인지 평가.

        간단 heuristic:
        - 기존 내용 대비 신규 내용이 30% 이상 더 길면서 구체적인 지시문 포함
        - 또는 새로운 섹션/명령어 추가

        Args:
            existing_content: 기존 SKILL.md 내용
            new_content: 신규 SKILL.md 내용

        Returns:
            True면 승인, False면 거절
        """
        if not existing_content:
            return True  # 최초 생성은 항상 승인

        # 길이 비교
        existing_len = len(existing_content.strip())
        new_len = len(new_content.strip())

        # 30% 이상 길어졌거나 300자 이상 추가
        if new_len > existing_len * 1.3 or (new_len - existing_len) > 300:
            # 새로운 섹션/명령어 포함 여부
            existing_sections = set(re.findall(r'^##\s+(.+)$', existing_content, re.MULTILINE))
            new_sections = set(re.findall(r'^##\s+(.+)$', new_content, re.MULTILINE))
            unique_sections = new_sections - existing_sections
            if unique_sections:
                log.info(f"✨ 홀드아웃 승인: {unique_sections} 섹션 추가")
                return True

        # 새로운 명령어/코드 블록 추가 확인
        existing_cmds = set(re.findall(r'`[^`]{3,}`', existing_content))
        new_cmds = set(re.findall(r'`[^`]{3,}`', new_content))
        if new_cmds - existing_cmds:
            log.info(f"✨ 홀드아웃 승인: 명령어/코드 추가")
            return True

        # 무효 변경: 길이 10% 이하 차이면 거절
        if abs(new_len - existing_len) < existing_len * 0.1:
            log.info(f"⏭️ 홀드아웃 거절: 변경량 부족 ({abs(new_len - existing_len)}자)")
            return False

        return True

    def _record_rejection(self, skill_name: str, reason: str):
        """거절 버퍼에 부정 피드백 기록."""
        rejection_path = SKILLS_ROOT.parent / "rejected_edits.jsonl"
        rejection_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now().isoformat(),
            "skill_name": skill_name or "new",
            "reason": reason,
        }
        with open(rejection_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info(f"📝 거절 기록: {skill_name} — {reason}")

    def _skill_exists(self, task_name: str) -> bool:
        """이미 같은 이름/내용의 스킬이 존재하는지 확인."""
        if not SKILLS_ROOT.exists():
            return False
        for skill_dir in SKILLS_ROOT.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
                if task_name.lower() in content.lower():
                    return True
            except Exception:
                continue
        return False

    def _is_stable(self, evidence: list) -> bool:
        """L3 패턴은 이미 3회 반복 검증을 통과했으므로, 한 건만 있어도 안정적.

        L3까지 승격된 패턴은 BioMemoryEngine에서 frequency >= threshold(기본 3) 조건을
        만족한 상태이며, Dreaming 사이클에서 호출되므로 추가 evidence 개수 체크 불필요.
        """
        return len(evidence) >= 1

    def maybe_evolve(
        self,
        task_name: str,
        evidence: list,
        frequency: int,
    ) -> Optional[Path]:
        """반복 패턴을 스킬로 승격.

        Args:
            task_name: 스킬 이름 (L3 패턴 요약 기반)
            evidence:  관련 에피소드 리스트
            frequency: 반복 횟수

        Returns:
            생성된 SKILL.md 경로 (None이면 조건 미충족)
        """
        if self._skill_exists(task_name):
            log.info(f"⏭️ 이미 존재하는 스킬 (스킵): {task_name[:40]}")
            return None

        if frequency < 2:
            log.debug(f"⏭️ 반복 횟수 부족 ({frequency}): {task_name[:40]}")
            return None

        if not self._is_stable(evidence):
            log.debug(f"⏭️ 증거 부족: {task_name[:40]}")
            return None

        # SkillOpt: 편집예산 확인
        name = _skill_name(task_name)
        if self._edit_budget(name) <= 0:
            log.info(f"⏭️ 편집예산 소진: {name} — 향후 개선 보류")
            self._record_rejection(name, "edit_budget_exhausted")
            return None

        # SkillOpt: 홀드아웃 검증 (기존 스킬이 있으면 품질 비교)
        existing_content = ""
        skill_dir = SKILLS_ROOT / name
        if skill_dir.exists() and (skill_dir / "SKILL.md").exists():
            existing_content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        # record_learning_interaction 으로 스킬 생성
        summary = evidence[0][:500] if evidence else task_name[:500]
        concepts = task_name.split()
        result = record_learning_interaction(
            question=f"반복 패턴 발전: {task_name}",
            answer_summary=summary,
            concepts=concepts,
            category=_classify(task_name),
        )

        if result is not None and existing_content:
            # 홀드아웃 검증: 신규 내용이 기존보다 나은지 확인
            new_content = result.read_text(encoding="utf-8") if result.is_file() else ""
            if not self._holdout_validate(existing_content, new_content):
                _rollback_skill(result.parent if result.is_file() else result)
                self._record_rejection(name, "holdout_rejected")
                log.info(f"⏭️ 홀드아웃 거절 → 롤백: {name}")
                return None

        if result:
            log.info(f"✨ Dreaming → 스킬 자동 생성/개선 완료: {result}")
        return result


# ── 큐레이션/통계 레이어 (modules/skill_curator_ext.py로 분리) ─
from modules.skill_curator_ext import (
    find_similar_skill, sync_to_vault,
    record_exec_recovery, record_learning_interaction,
    get_skill_stats, prune_old_skills, get_weekly_usage_stats,
    curate_skills, consolidate_skills, get_curator_report,
    search_skills, _validate_skill, _rollback_skill,
)

__all__ = [
    "SkillEvolver",
    "find_similar_skill", "sync_to_vault",
    "record_exec_recovery", "record_learning_interaction",
    "get_skill_stats", "prune_old_skills", "get_weekly_usage_stats",
    "curate_skills", "consolidate_skills", "get_curator_report",
    "search_skills",
]
