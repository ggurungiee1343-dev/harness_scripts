# modules/curator.py
# Hermes3 Phase 2 - 스킬 큐레이터 + 코드베이스 편집자 (2026-06-08 확장)
#
# ── 연동 구조 ──────────────────────────────────────────────────────────────
# [호출 경로]
#   텔레그램 버튼/명령
#     └─ handlers/_base.py (🧹 큐레이터 버튼)
#           └─ handlers/_system.py (cmd_curate)
#                 └─ SkillCurator.full_audit()  ← 통합 리포트 반환
#
# [skill_evolver.py 와의 분업]
#   skill_evolver.curate_skills()  → ~/.hermes/governance/skills/ 하위 SKILL.md 관리
#   curator.SkillCurator           → ~/.hermes/skills/*.json 관리 + 코드베이스 스캔
#
# [사용 방법]
#   텔레그램: 🧹 큐레이터 버튼 → full_audit() 리포트 수신
#   직접 실행: python3 -c "from modules.curator import SkillCurator; print(SkillCurator().full_audit())"
#   Claude Code: Read로 확인 후 개선 사항 반영
# ──────────────────────────────────────────────────────────────────────────

import json, os, glob, shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

SCRIPTS_ROOT = Path("/Users/bluesea/Applications/Mjauto/Scripts")
MODULES_ROOT = SCRIPTS_ROOT / "modules"
HANDLERS_ROOT = SCRIPTS_ROOT / "handlers"

# 코드베이스 경보 기준
LINE_WARN_THRESHOLD = 600    # 경고 (리팩토링 검토 권장)
LINE_ALERT_THRESHOLD = 900   # 위험 (분할 강력 권장)
BAK_SEARCH_ROOTS = [
    SCRIPTS_ROOT,              # 루트 .bak 파일
    SCRIPTS_ROOT / "modules",
    SCRIPTS_ROOT / "handlers",
    SCRIPTS_ROOT / "_archive",
    SCRIPTS_ROOT / "hermes" / "memory_engine",
]
# _backup/ 폴더는 의도적 백업이므로 스캔 제외


class SkillCurator:
    SKILL_DIR = os.path.expanduser("~/.hermes/skills")
    ARCHIVE_DIR = os.path.expanduser("~/.hermes/skills/archive")

    def __init__(self):
        os.makedirs(self.SKILL_DIR, exist_ok=True)
        os.makedirs(self.ARCHIVE_DIR, exist_ok=True)

    # ── 스킬 관리 (기존) ────────────────────────────────────────────────────

    def create_skill(self, name: str, description: str,
                     trigger_patterns: List[str], actions: List[Dict]) -> str:
        """새 스킬 생성 → ~/.hermes/skills/{name}.json"""
        skill = {
            "name": name, "version": "1.0",
            "created": datetime.now().isoformat(), "last_used": None,
            "description": description,
            "trigger": {"patterns": trigger_patterns},
            "actions": actions, "usage_count": 0
        }
        path = os.path.join(self.SKILL_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(skill, f, indent=2, ensure_ascii=False)
        return path

    def load_skill(self, name: str) -> Optional[Dict]:
        """스킬 로드 + usage_count 증가"""
        path = os.path.join(self.SKILL_DIR, f"{name}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            skill = json.load(f)
        skill["usage_count"] = skill.get("usage_count", 0) + 1
        skill["last_used"] = datetime.now().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(skill, f, indent=2, ensure_ascii=False)
        return skill

    def list_skills(self) -> List[Dict]:
        """전체 스킬 목록 (사용 빈도 내림차순)"""
        skills = []
        for f in glob.glob(os.path.join(self.SKILL_DIR, "*.json")):
            with open(f, encoding="utf-8") as fp:
                skills.append(json.load(fp))
        return sorted(skills, key=lambda x: x.get("usage_count", 0), reverse=True)

    def audit_skills(self) -> Dict:
        """중복 트리거 패턴 탐지 + 유효성 검사"""
        skills = self.list_skills()
        seen_patterns: Dict[str, str] = {}
        conflicts = []
        for s in skills:
            for p in s["trigger"]["patterns"]:
                if p in seen_patterns:
                    conflicts.append({"pattern": p, "skills": [seen_patterns[p], s["name"]]})
                else:
                    seen_patterns[p] = s["name"]
        return {"total": len(skills), "conflicts": conflicts}

    def archive_unused(self, days_threshold: int = 90) -> List[str]:
        """마지막 사용 후 days_threshold일 이상 미사용 스킬 아카이브"""
        threshold = datetime.now() - timedelta(days=days_threshold)
        archived = []
        for f in glob.glob(os.path.join(self.SKILL_DIR, "*.json")):
            with open(f, encoding="utf-8") as fp:
                skill = json.load(fp)
            last_used = skill.get("last_used")
            if last_used is None or datetime.fromisoformat(last_used) < threshold:
                dest = os.path.join(self.ARCHIVE_DIR, os.path.basename(f))
                shutil.move(f, dest)
                archived.append(skill["name"])
        return archived

    def match_trigger(self, text: str) -> Optional[Dict]:
        """텍스트와 매칭되는 스킬 반환 (natural_language_router 연동용)"""
        for skill in self.list_skills():
            for pattern in skill["trigger"]["patterns"]:
                if pattern.lower() in text.lower():
                    return skill
        return None

    # ── 코드베이스 스캔 (신규 2026-06-08) ───────────────────────────────────

    def scan_large_files(self) -> List[Dict]:
        """300줄 초과 Python 파일 목록 반환 (경고/위험 등급 분류)

        기사 '취향(Taste)이 새로운 10x' 인사이트 적용:
        에이전트가 무한 생성한 대형 파일을 탐지하여 리팩토링 후보를 제시.
        300줄 이상만 보고 (핵심 모듈 집중 관리).
        """
        results = []
        scan_dirs = [SCRIPTS_ROOT, MODULES_ROOT, HANDLERS_ROOT]
        seen = set()
        for d in scan_dirs:
            if not d.exists():
                continue
            for f in d.glob("*.py"):
                if f in seen or f.stat().st_size == 0:
                    continue
                seen.add(f)
                try:
                    lines = len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
                except Exception:
                    continue
                if lines >= 300:
                    if lines >= LINE_ALERT_THRESHOLD:
                        grade = "🔴 위험"
                    elif lines >= LINE_WARN_THRESHOLD:
                        grade = "🟡 경고"
                    else:
                        grade = "🟢 주의"
                    results.append({
                        "file": str(f.relative_to(SCRIPTS_ROOT)),
                        "lines": lines,
                        "grade": grade
                    })
        return sorted(results, key=lambda x: x["lines"], reverse=True)

    def scan_bak_files(self) -> List[Dict]:
        """.bak / _old / _test 임시 파일 탐지 (의도적 _backup/ 폴더 제외)

        에이전트가 수정 전 자동 생성한 .bak 파일이 쌓이면
        코드베이스 오염 및 혼란의 원인이 됨. 주기적 정리 제안.
        """
        patterns = ["*.bak*", "*.bak", "*_old.py", "*_test.py"]
        found = []
        for root in BAK_SEARCH_ROOTS:
            if not Path(root).exists():
                continue
            for pat in patterns:
                for f in Path(root).glob(pat):
                    # _backup/ 폴더는 의도적 보관 — 제외
                    if "_backup" in str(f):
                        continue
                    try:
                        size_kb = round(f.stat().st_size / 1024, 1)
                        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
                    except Exception:
                        size_kb, mtime = 0, "unknown"
                    found.append({
                        "file": str(f.relative_to(SCRIPTS_ROOT)),
                        "size_kb": size_kb,
                        "modified": mtime
                    })
        return sorted(found, key=lambda x: x["modified"])

    def full_audit(self) -> str:
        """스킬 감사 + 대형 파일 + .bak 파일 통합 리포트 (텔레그램 출력용)

        [사용 방법]
        텔레그램: 🧹 큐레이터 버튼 → 이 리포트 수신
        직접: python3 -c "from modules.curator import SkillCurator; print(SkillCurator().full_audit())"
        """
        lines = ["🧹 **코드베이스 큐레이터 리포트**\n"]

        # 1. 스킬 감사
        skill_result = self.audit_skills()
        lines.append(f"📦 **스킬 현황**: 총 {skill_result['total']}개")
        if skill_result["conflicts"]:
            lines.append(f"  ⚠️ 중복 트리거 {len(skill_result['conflicts'])}건:")
            for c in skill_result["conflicts"]:
                lines.append(f"    · `{c['pattern']}` → {c['skills']}")
        else:
            lines.append("  ✅ 중복 트리거 없음")

        # 2. 대형 파일 스캔
        large = self.scan_large_files()
        alert = [f for f in large if "위험" in f["grade"]]
        warn  = [f for f in large if "경고" in f["grade"]]
        lines.append(f"\n📏 **대형 파일** (300줄+): {len(large)}개")
        lines.append(f"  🔴 위험(900줄+): {len(alert)}개  🟡 경고(600줄+): {len(warn)}개")
        for f in large[:8]:  # 상위 8개만 표시
            lines.append(f"  {f['grade']} `{f['file']}` — {f['lines']}줄")
        if len(large) > 8:
            lines.append(f"  … 외 {len(large)-8}개")

        # 3. .bak 파일 스캔
        baks = self.scan_bak_files()
        lines.append(f"\n🗑️ **.bak 임시 파일**: {len(baks)}개")
        if baks:
            lines.append("  ⚠️ 정리 권장 (Claude Code 또는 수동 삭제):")
            for b in baks:
                lines.append(f"  · `{b['file']}` ({b['size_kb']}KB, {b['modified']})")
        else:
            lines.append("  ✅ 임시 파일 없음")

        lines.append("\n💡 대형 파일 수정 시 전체 구조 파악 후 부분 수정(Edit) 사용")
        lines.append("💡 .bak 파일 발견 시 Claude Code에서 삭제 후 05_시스템상태.md에 기록")
        return "\n".join(lines)
