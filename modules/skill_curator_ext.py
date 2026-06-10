"""
Skill Curator Extension — skill_evolver.py에서 분리 (2026-06-09)
큐레이션/통계/검증/롤백 레이어
"""

import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import hashlib

# 순환 import 방지: skill_evolver의 공유 상수/헬퍼를 직접 정의
SKILLS_ROOT  = Path("/Users/bluesea/.hermes/skills/learned")
VAULT_SKILLS = Path("/Users/bluesea/Applications/Mjobsidian/wiki/10_AI_Automation/skills")
SESSION_LOG  = Path("/Users/bluesea/.hermes/session_events.jsonl")

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

_CATEGORIES = {
    "file_ops": ["file", "move", "copy", "create", "read", "write", "ls", "mv", "cp", "rm", "mkdir", "chmod"],
    "network":  ["curl", "wget", "ping", "ssh", "scp", "http", "api", "download"],
    "python":   ["python", "pip", "conda", "import", "module", "virtualenv", "pytest"],
    "system":   ["brew", "launchctl", "system", "process", "kill", "ps", "top", "df"],
    "docker":   ["docker", "container", "image", "compose", "dockerfile"],
    "llm":      ["llama", "gguf", "ollama", "model", "token", "inference"],
    "obsidian": ["obsidian", "vault", "markdown", "wiki", "note"],
    "telegram": ["telegram", "bot", "message", "chat", "webhook"],
    "git":      ["git", "commit", "push", "pull", "branch", "merge"],
}

def _classify(cmd: str) -> str:
    cmd_lower = cmd.lower()
    for cat, keywords in _CATEGORIES.items():
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
    if not SKILLS_ROOT.exists():
        return []
    results = []
    for sd in SKILLS_ROOT.iterdir():
        if not sd.is_dir() or sd.name == skill_name:
            continue
        sm = sd / "SKILL.md"
        if not sm.exists():
            continue
        try:
            content = sm.read_text(encoding="utf-8")
            if f"**카테고리**: {category}" in content:
                results.append(sd.name)
        except Exception:
            continue
        if len(results) >= limit:
            break
    return results

log = logging.getLogger("skill_evolver")

WEEKLY_USAGE_THRESHOLD = 2
ARCHIVE_ROOT = SKILLS_ROOT.parent / "archived_skills"


def find_similar_skill(problem: str) -> Optional[Path]:
    """키워드 기반으로 기존 유사 SKILL.md 탐색."""
    if not SKILLS_ROOT.exists():
        return None
    keywords = set(problem.lower().split())
    best_score, best_path = 0, None
    for skill_md in SKILLS_ROOT.rglob("SKILL.md"):
        try:
            content = skill_md.read_text(encoding="utf-8").lower()
            score = sum(1 for kw in keywords if kw in content)
            if score > best_score and score >= 3:
                best_score, best_path = score, skill_md.parent
        except Exception:
            continue
    return best_path


def sync_to_vault(skill_dir: Path) -> None:
    """생성된 SKILL.md 를 옵시디언 위키에 복사 (충돌 해결 포함)."""
    try:
        VAULT_SKILLS.mkdir(parents=True, exist_ok=True)
        dst = VAULT_SKILLS / skill_dir.name
        src_md = skill_dir / "SKILL.md"

        if not dst.exists():
            shutil.copytree(str(skill_dir), str(dst))
            log.info(f"Vault 동기화: {dst}")
        else:
            dst_md = dst / "SKILL.md"
            if src_md.exists():
                src_time = src_md.stat().st_mtime
                dst_time = dst_md.stat().st_mtime if dst_md.exists() else 0
                if src_time > dst_time:
                    shutil.copy2(str(src_md), str(dst_md))
                    log.info(f"Vault SKILL.md 갱신: {dst_md}")
                else:
                    log.debug(f"Vault SKILL.md 최신, 스킵: {dst_md}")
    except Exception as e:
        log.warning(f"Vault 동기화 실패 (무시): {e}")


def record_exec_recovery(
    original_cmd: str,
    error_msg: str,
    fixed_cmd: str,
    retries: int,
) -> Optional[Path]:
    """/exec 에러 복구 성공 시 SKILL.md 자동 생성."""
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)

    problem  = f"에러: {error_msg[:100]} (원본 명령: {original_cmd[:60]})"
    solution = f"복구 명령어: {fixed_cmd}"
    keywords = " | ".join(original_cmd.split()[:6])
    timestamp = datetime.now().isoformat()
    name = _skill_name(problem)
    category = _classify(original_cmd)

    similar = find_similar_skill(problem)
    if similar:
        patch_block = (
            f"\n## 패치 [{timestamp}] — error_recovery\n"
            f"**추가 복구 명령어**: `{fixed_cmd}`\n"
            f"**원본 실패 명령어**: `{original_cmd}`\n"
            f"**에러**: {error_msg[:120]}\n"
            f"재시도={retries}회\n---\n"
        )
        with open(similar / "SKILL.md", "a", encoding="utf-8") as f:
            f.write(patch_block)
        log.info(f"SKILL.md 패치 완료: {similar.name}")
        sync_to_vault(similar)
        return similar / "SKILL.md"

    skill_dir = SKILLS_ROOT / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"

    related_text = ", ".join(_find_related(name, category)) or "없음"
    skill_md_path.write_text(
        SKILL_MD_TEMPLATE.format(
            skill_name=name, timestamp=timestamp, trigger="error_recovery",
            category=category, original_cmd=original_cmd,
            error_msg=error_msg[:200], fixed_cmd=fixed_cmd, retries=retries,
            problem=problem, solution=solution, keywords=keywords, related=related_text,
        ),
        encoding="utf-8",
    )
    log.info(f"✨ 신규 SKILL.md 생성: {skill_md_path} (카테고리: {category})")

    if not _validate_skill(skill_dir):
        _rollback_skill(skill_dir)
        return None

    event = {
        "timestamp": timestamp, "trigger": "error_recovery", "category": category,
        "original_cmd": original_cmd, "fixed_cmd": fixed_cmd,
        "error_msg": error_msg[:200], "retries": retries, "skill_path": str(skill_md_path),
    }
    with open(SESSION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    sync_to_vault(skill_dir)
    return skill_md_path


def record_learning_interaction(
    question: str,
    answer_summary: str,
    concepts: list,
    category: str = "general",
) -> Optional[Path]:
    """대화 기반 학습 스킬 생성."""
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().isoformat()
    name = _skill_name(question)
    skill_dir = SKILLS_ROOT / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"

    concepts_str = ", ".join(concepts[:5])
    skill_md_path.write_text(
        LEARNING_SKILL_TEMPLATE.format(
            skill_name=name, timestamp=timestamp, category=category,
            question=question[:200], concepts=concepts_str,
            summary=answer_summary[:500],
            application=f"카테고리: {category}\n관련 개념: {concepts_str}",
            references=f"학습 시간: {timestamp}",
        ),
        encoding="utf-8",
    )
    log.info(f"🧠 학습 스킬 생성: {skill_md_path} (카테고리: {category})")

    if not _validate_skill(skill_dir):
        _rollback_skill(skill_dir)
        return None

    event = {
        "timestamp": timestamp, "trigger": "learning_interaction",
        "category": category, "question": question[:200],
        "concepts": concepts[:5], "skill_path": str(skill_md_path),
    }
    with open(SESSION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    sync_to_vault(skill_dir)
    return skill_md_path


def get_skill_stats() -> dict:
    """스킬 통계 반환."""
    if not SKILLS_ROOT.exists():
        return {"total": 0, "by_category": {}, "recent": [], "oldest": []}

    skills = []
    for skill_dir in SKILLS_ROOT.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
            created = skill_md.stat().st_mtime
            trigger = "unknown"
            category = "general"
            for line in content.split("\n"):
                if "**트리거**" in line:
                    trigger = line.split(":")[-1].strip()
                if "**카테고리**" in line:
                    category = line.split(":")[-1].strip()
            skills.append({
                "name": skill_dir.name, "path": str(skill_md),
                "created": datetime.fromtimestamp(created),
                "trigger": trigger, "category": category,
            })
        except Exception:
            continue

    by_category: dict = {}
    for s in skills:
        cat = s["category"]
        by_category[cat] = by_category.get(cat, 0) + 1

    skills.sort(key=lambda s: s["created"], reverse=True)
    return {
        "total": len(skills),
        "by_category": by_category,
        "recent": [s["name"] for s in skills[:5]],
        "oldest": [s["name"] for s in skills[-5:] if len(skills) > 5],
    }


def prune_old_skills(days: int = 90, dry_run: bool = True) -> list:
    """일정 기간 이상 사용되지 않은 스킬 정리."""
    if not SKILLS_ROOT.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days)
    targets = []

    for skill_dir in SKILLS_ROOT.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        mtime = datetime.fromtimestamp(skill_md.stat().st_mtime)
        if mtime < cutoff:
            targets.append(str(skill_dir))

    if not dry_run:
        for t in targets:
            path = Path(t)
            if path.exists():
                shutil.rmtree(path)
                log.info(f"🗑️ 오래된 스킬 삭제: {path.name}")

    return targets


def get_weekly_usage_stats() -> dict:
    """session_events.jsonl 분석 → 스킬별 주간 사용 통계."""
    if not SESSION_LOG.exists():
        return {}

    cutoff = datetime.now() - timedelta(days=7)
    usage: dict = {}

    with open(SESSION_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = event.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if dt < cutoff:
                continue
            skill_path = event.get("skill_path", "")
            trigger = event.get("trigger", "")
            category = event.get("category", "")
            key = Path(skill_path).parent.name if skill_path else trigger
            if not key:
                continue
            if key not in usage:
                usage[key] = {"count": 0, "category": category, "last_used": ts}
            usage[key]["count"] += 1
            if ts > usage[key]["last_used"]:
                usage[key]["last_used"] = ts

    return usage


def curate_skills(dry_run: bool = True) -> dict:
    """주간 큐레이션: 저사용 스킬 아카이브."""
    if not SKILLS_ROOT.exists():
        return {"archived": [], "kept": [], "stats": {}}

    usage = get_weekly_usage_stats()
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

    archived = []
    kept = []

    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue
        name = skill_dir.name
        count = usage.get(name, {}).get("count", 0)
        if count < WEEKLY_USAGE_THRESHOLD:
            archived.append({"name": name, "count": count})
            if not dry_run:
                archive_dst = ARCHIVE_ROOT / name
                if archive_dst.exists():
                    shutil.rmtree(archive_dst)
                shutil.move(str(skill_dir), str(archive_dst))
                log.info(f"📦 아카이브: {name} (주간 사용 {count}회)")
        else:
            kept.append({"name": name, "count": count})

    log.info(f"📊 Curator: 아카이브 {len(archived)}개 / 유지 {len(kept)}개 (dry_run={dry_run})")
    return {
        "archived": archived, "kept": kept,
        "stats": {"total": len(archived) + len(kept),
                  "archived_count": len(archived), "kept_count": len(kept)},
    }


def consolidate_skills(category: str = None, dry_run: bool = True) -> list:
    """동일 카테고리 내 유사 스킬 통합."""
    if not SKILLS_ROOT.exists():
        return []

    groups: dict = {}
    for skill_dir in SKILLS_ROOT.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue
        cat = "general"
        for line in content.split("\n"):
            if "**카테고리**" in line:
                cat = line.split(":")[-1].strip()
                break
        if category and cat != category:
            continue
        name = skill_dir.name
        core = "_".join(name.split("_")[:-1]) if "_" in name else name
        key = f"{cat}:{core}"
        if key not in groups:
            groups[key] = []
        groups[key].append({"name": name, "path": str(skill_dir), "content": content[:200]})

    targets = [g for g in groups.values() if len(g) >= 2]

    if not dry_run:
        for group in targets:
            primary = Path(group[0]["path"])
            primary_md = primary / "SKILL.md"
            for dup in group[1:]:
                dup_path = Path(dup["path"])
                dup_md = dup_path / "SKILL.md"
                if dup_md.exists():
                    extra = dup_md.read_text(encoding="utf-8")
                    with open(primary_md, "a", encoding="utf-8") as f:
                        f.write(f"\n\n---\n## 통합: {dup['name']}\n{extra[:500]}\n")
                    shutil.rmtree(dup_path)
                    log.info(f"🔗 통합: {dup['name']} → {group[0]['name']}")

    return targets


def get_curator_report() -> str:
    """큐레이터 종합 보고서 생성."""
    usage = get_weekly_usage_stats()
    stats = get_skill_stats()

    lines = [
        "## 📊 Curator 보고서",
        f"- 전체 스킬: {stats['total']}개",
        f"- 카테고리 분포: {stats['by_category']}",
        f"- 최근 생성: {', '.join(stats['recent'])}",
        "",
        "### 주간 사용 현황",
    ]

    sorted_usage = sorted(usage.items(), key=lambda x: x[1]["count"], reverse=True)
    for name, info in sorted_usage[:15]:
        lines.append(f"- `{name}`: {info['count']}회 (카테고리: {info['category']})")

    if not sorted_usage:
        lines.append("- 기록 없음 (7일 이내)")

    low_usage = [(n, u) for n, u in usage.items() if u["count"] < WEEKLY_USAGE_THRESHOLD]
    if low_usage:
        lines.extend(["", f"### ⚠️ 저사용 후보 (< {WEEKLY_USAGE_THRESHOLD}회/주)"])
        for name, info in low_usage:
            lines.append(f"- `{name}`: {info['count']}회")

    return "\n".join(lines)


def search_skills(keyword: str, limit: int = 10) -> list:
    """스킬 내용 검색."""
    if not SKILLS_ROOT.exists():
        return []

    results = []
    for skill_md in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        try:
            content = skill_md.read_text(encoding="utf-8")
            if keyword.lower() in content.lower():
                name = skill_md.parent.name
                snippet = next(
                    (line.strip()[:120] for line in content.split("\n")
                     if keyword.lower() in line.lower()),
                    content[:100]
                )
                results.append({"name": name, "path": str(skill_md), "snippet": snippet})
                if len(results) >= limit:
                    break
        except Exception:
            continue

    return results


def _validate_skill(skill_dir: Path) -> bool:
    """SKILL.md가 유효한지 검증."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        log.warning(f"❌ 검증 실패: SKILL.md 없음 — {skill_dir.name}")
        return False
    try:
        content = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"❌ 검증 실패: 읽기 오류 — {skill_dir.name}: {e}")
        return False
    if not content.strip() or len(content.strip()) < 50:
        log.warning(f"❌ 검증 실패: 내용 부족 — {skill_dir.name}")
        return False
    if not content.strip().startswith("# SKILL:"):
        log.warning(f"❌ 검증 실패: '# SKILL:' 헤더 누락 — {skill_dir.name}")
        return False
    for key in ["**생성일**", "**트리거**", "**카테고리**"]:
        if key not in content:
            log.warning(f"❌ 검증 실패: 필드 '{key}' 누락 — {skill_dir.name}")
            return False
    log.info(f"✅ SKILL.md 검증 통과: {skill_dir.name}")
    return True


def _rollback_skill(skill_dir: Path) -> None:
    """생성된 스킬 디렉토리를 삭제하여 원자적으로 롤백."""
    try:
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            log.warning(f"↩️ 롤백 완료: {skill_dir.name} 삭제됨")
    except Exception as e:
        log.error(f"❌ 롤백 실패: {skill_dir.name}: {e}")
