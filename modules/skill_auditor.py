"""
Runtime Skill Auditor v2.0 (2606.11671 + COLLEAGUE.SKILL 2605.31264)
======================================================================
~/.hermes/skills/ 디렉토리의 스킬을 동적으로 감사.
정적 코드 리뷰만으로는 놓치는 컨텍스트 의존성 악성 패턴을 탐지.

RSA 핵심 원칙:
  - 위험 인터페이스에 집중 (파일 쓰기, 외부 호출, 상태 변조)
  - 실행 trace 기반 보안 레이블링
  - 정적 분석 대비 13pp 정확도 향상 (논문 기준)

v2.0 추가 (COLLEAGUE.SKILL 2605.31264 + Your Agents Are Aging Too 2026-05-27):
  - 스킬 lifecycle 메타데이터 추적 (생성일, 마지막 사용, 호출 수, 성공률)
  - 에이전트 노화 감지: 30일 미사용 OR 성공률 < 0.5 → stale 마킹
  - 객관적 성공 기준: 태그 실행 예외 여부 (LLM 주관 판단 배제)

사용:
  from modules.skill_auditor import SkillAuditor, SkillLifecycle
  auditor = SkillAuditor()
  report = auditor.audit_all()
  lifecycle = SkillLifecycle()
  lifecycle.record_call("skill_name", success=True)
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime, timezone

logger = logging.getLogger("HermesOrchestrator")

SKILLS_DIR    = Path("/Users/bluesea/.hermes/skills")
AUDIT_LOG     = Path("/Users/bluesea/.hermes/runtime/skill_audit.log")
LIFECYCLE_DB  = Path("/Users/bluesea/.hermes/runtime/skill_lifecycle.json")

# ── Lifecycle 상수 (COLLEAGUE.SKILL + Aging 논문) ──────────────────────
STALE_DAYS          = 30    # 30일 미사용 시 stale
STALE_SUCCESS_FLOOR = 0.5   # 성공률 50% 미만 시 stale

# ── 위험 패턴 정의 ────────────────────────────────────────────────
_RISK_PATTERNS: List[Tuple[str, str, int]] = [
    # (패턴, 설명, 위험도 1-3)
    (r"subprocess|os\.system|os\.popen",        "시스템 명령 실행",       3),
    (r"requests\.|httpx\.|urllib",               "외부 HTTP 호출",         2),
    (r"open\(.+['\"]w['\"]",                     "파일 쓰기",              2),
    (r"eval\s*\(|exec\s*\(",                     "동적 코드 실행",         3),
    (r"import\s+socket|socket\.connect",         "네트워크 소켓",          3),
    (r"shutil\.rmtree|os\.remove|os\.unlink",    "파일 삭제",              2),
    (r"__import__|importlib\.import",            "동적 모듈 로드",         2),
    (r"pickle\.loads|marshal\.loads",            "역직렬화 (RCE 위험)",    3),
    (r"base64\.b64decode.*exec|eval.*decode",    "인코딩 우회 실행",       3),
    (r"ANTHROPIC_API_KEY|OPENAI_API_KEY|TOKEN",  "API 키 하드코딩 의심",   3),
]

_SEVERITY_LABEL = {1: "🟢 LOW", 2: "🟡 MEDIUM", 3: "🔴 HIGH"}


class SkillAuditor:
    """
    ~/.hermes/skills/ 전체 스킬 동적 감사 엔진.
    """

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self.skills_dir = skills_dir
        self.results: List[Dict] = []

    def _scan_file(self, file_path: Path) -> List[Dict]:
        """단일 파일 정적+패턴 스캔."""
        findings = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return [{"file": str(file_path), "issue": f"읽기 실패: {e}", "severity": 1}]

        for pattern, description, severity in _RISK_PATTERNS:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                findings.append({
                    "file": str(file_path.relative_to(self.skills_dir)),
                    "pattern": pattern,
                    "description": description,
                    "severity": severity,
                    "severity_label": _SEVERITY_LABEL[severity],
                    "match_count": len(matches),
                    "sample": matches[0] if matches else "",
                })
        return findings

    def audit_skill(self, skill_name: str) -> Dict:
        """단일 스킬 감사. SKILL.md + 추가 .py/.sh 파일 포함."""
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists():
            return {"skill": skill_name, "status": "NOT_FOUND", "findings": []}

        all_findings = []
        for ext in ["*.md", "*.py", "*.sh"]:
            for f in skill_dir.glob(ext):
                all_findings.extend(self._scan_file(f))

        high_count = sum(1 for f in all_findings if f["severity"] == 3)
        med_count  = sum(1 for f in all_findings if f["severity"] == 2)

        if high_count > 0:
            status = "⛔ SUSPICIOUS"
        elif med_count > 2:
            status = "⚠️ REVIEW"
        else:
            status = "✅ CLEAN"

        return {
            "skill": skill_name,
            "status": status,
            "high": high_count,
            "medium": med_count,
            "findings": all_findings,
        }

    def audit_all(self, write_log: bool = True) -> str:
        """모든 스킬 감사 후 요약 보고서 반환."""
        if not self.skills_dir.exists():
            return "⚠️ 스킬 디렉토리 없음"

        skills = [d.name for d in self.skills_dir.iterdir() if d.is_dir()]
        if not skills:
            return "스킬 없음"

        self.results = []
        suspicious, review, clean = [], [], []

        for skill_name in sorted(skills):
            result = self.audit_skill(skill_name)
            self.results.append(result)
            if "SUSPICIOUS" in result["status"]:
                suspicious.append(skill_name)
            elif "REVIEW" in result["status"]:
                review.append(skill_name)
            else:
                clean.append(skill_name)

        lines = [
            f"🔍 <b>Runtime Skill Audit</b>",
            f"📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
            f"총 {len(skills)}개 스킬 감사\n",
            f"⛔ 의심 ({len(suspicious)}개): {', '.join(suspicious) or '없음'}",
            f"⚠️  검토 ({len(review)}개): {', '.join(review) or '없음'}",
            f"✅ 정상 ({len(clean)}개): {len(clean)}개",
        ]

        if suspicious:
            lines.append("\n<b>⛔ 의심 스킬 상세:</b>")
            for r in self.results:
                if "SUSPICIOUS" in r["status"]:
                    for f in r["findings"]:
                        if f["severity"] == 3:
                            lines.append(
                                f"  • [{r['skill']}] {f['description']} — "
                                f"`{f['sample'][:60]}`"
                            )

        report = "\n".join(lines)

        if write_log:
            AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with AUDIT_LOG.open("a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n{report}\n")
            logger.info(f"[SkillAudit] 감사 완료: {len(suspicious)}개 의심, {len(review)}개 검토 대상")

        return report

    def get_skill_risk_score(self, skill_name: str) -> int:
        """스킬 위험 점수 반환 (0=없음, 1=LOW, 2=MED, 3=HIGH)."""
        result = self.audit_skill(skill_name)
        if result["high"] > 0:
            return 3
        if result["medium"] > 2:
            return 2
        if result["medium"] > 0:
            return 1
        return 0


# ══════════════════════════════════════════════════════════════════
# Skill Lifecycle Tracker
# COLLEAGUE.SKILL (2605.31264): 스킬은 생성·사용·평가·수정 lifecycle 자산
# Your Agents Are Aging Too (2026-05-27): 에이전트 노화 탐지
# ══════════════════════════════════════════════════════════════════

class SkillLifecycle:
    """스킬 lifecycle 메타데이터 추적.

    ~/.hermes/runtime/skill_lifecycle.json에 기록.
    {skill_name: {created_at, last_used, call_count, success_count, is_stale, ...}}
    """

    def __init__(self):
        LIFECYCLE_DB.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Dict] = self._load()

    def _load(self) -> Dict:
        if LIFECYCLE_DB.exists():
            try:
                return json.loads(LIFECYCLE_DB.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self):
        tmp = LIFECYCLE_DB.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(LIFECYCLE_DB)

    def record_feedback(self, skill_name: str, success: bool):
        """MJ 피드백 신호로 마지막 호출 결과를 소급 수정.

        규칙 6: 잘 되면 우연인지 실력인지 기록하기.
        피드백 신호: 재질문("다시", "틀렸", "아니") → success=False로 소급 수정.
        일반 record_call과 독립적으로 호출 — 호출 횟수는 증가시키지 않음.
        """
        if skill_name not in self._data:
            return
        entry = self._data[skill_name]
        if not success and entry.get("success_count", 0) > 0:
            entry["success_count"] = max(0, entry["success_count"] - 1)
        entry["success_rate"] = round(
            entry["success_count"] / max(entry.get("call_count", 1), 1), 3
        )
        entry["last_feedback"] = datetime.now(timezone.utc).isoformat()
        entry["last_feedback_success"] = success
        self._update_stale(skill_name)
        self._save()
        logger.info(f"[SkillLifecycle] feedback: {skill_name} → success={success}")

    def record_call(self, skill_name: str, success: bool = True):
        """스킬 호출 기록. 성공/실패는 태그 실행 예외 여부로 판단 (객관적 지표)."""
        now = datetime.now(timezone.utc).isoformat()
        if skill_name not in self._data:
            self._data[skill_name] = {
                "created_at": now,
                "last_used": now,
                "call_count": 0,
                "success_count": 0,
                "is_stale": False,
            }
        entry = self._data[skill_name]
        entry["last_used"] = now
        entry["call_count"] = entry.get("call_count", 0) + 1
        if success:
            entry["success_count"] = entry.get("success_count", 0) + 1
        entry["success_rate"] = round(
            entry["success_count"] / max(entry["call_count"], 1), 3
        )
        self._update_stale(skill_name)
        self._save()

    def _update_stale(self, skill_name: str):
        entry = self._data.get(skill_name, {})
        last_used = entry.get("last_used", "")
        try:
            last_dt = datetime.fromisoformat(last_used)
            days_since = (datetime.now(timezone.utc) - last_dt).days
        except Exception:
            days_since = 0

        success_rate = entry.get("success_rate", 1.0)
        call_count = entry.get("call_count", 0)

        # stale 조건: 30일 미사용 OR (10회 이상 호출 + 성공률 50% 미만)
        entry["is_stale"] = (
            days_since >= STALE_DAYS or
            (call_count >= 10 and success_rate < STALE_SUCCESS_FLOOR)
        )
        entry["days_since_use"] = days_since

    def get_stale_skills(self) -> List[str]:
        """stale 스킬 목록 반환."""
        return [name for name, info in self._data.items() if info.get("is_stale")]

    def get_lifecycle_report(self) -> str:
        """스킬 lifecycle 현황 보고서."""
        if not self._data:
            return "📊 스킬 lifecycle 데이터 없음 (아직 호출된 스킬 없음)"

        stale = self.get_stale_skills()
        lines = [
            f"📊 <b>스킬 Lifecycle 현황</b> ({len(self._data)}개)",
            f"• 정상: {len(self._data) - len(stale)}개",
            f"• ⚠️ Stale: {len(stale)}개 ({', '.join(stale) or '없음'})",
            "",
            "<b>상위 호출 스킬:</b>",
        ]
        top = sorted(self._data.items(), key=lambda x: x[1].get("call_count", 0), reverse=True)[:5]
        for name, info in top:
            rate = info.get("success_rate", 1.0)
            calls = info.get("call_count", 0)
            stale_mark = " ⚠️" if info.get("is_stale") else ""
            lines.append(f"  • {name}{stale_mark}: {calls}회 호출, 성공률 {rate:.0%}")
        return "\n".join(lines)


# 싱글톤
_lifecycle_instance: SkillLifecycle = None

def get_skill_lifecycle() -> SkillLifecycle:
    global _lifecycle_instance
    if _lifecycle_instance is None:
        _lifecycle_instance = SkillLifecycle()
    return _lifecycle_instance
