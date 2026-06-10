"""
dream_scheduler.py — Self-Healing Loop v1.0
============================================
4-phase ExecPlan: A(진단) → B(수정) → C(검증) → D(보고)
- 트리거: launchd 새벽 3시 자동 실행 / 수동 /dreaming
- 최대 3회 재시도, 수정 전 백업, 확인 필요 항목 별도 저장

Usage:
    python3 -m modules.dream_scheduler         # CLI 실행
    from modules.dream_scheduler import run_self_healing
    await run_self_healing(context, chat_id)   # 텔레그램 핸들러에서 호출
"""

import asyncio
import datetime
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Optional

logger = logging.getLogger('HermesOrchestrator')

# ── 경로 상수 ──────────────────────────────────────────────
SCRIPTS_DIR = Path.home() / "Applications" / "Mjauto" / "Scripts"
MODULES_DIR = SCRIPTS_DIR / "modules"
BACKUP_DIR = Path.home() / "hermes_backups"
DB_PATH = Path.home() / ".hermes" / "runtime" / "hermes_index.db"
TESTS_DIR = SCRIPTS_DIR / "tests"

# ── 패턴 ──────────────────────────────────────────────────
LOG_SCAN_PATTERNS = {
    "parse_mode": re.compile(r"parse_mode|parse_mode.*missing|Can't parse entities", re.IGNORECASE),
    "import_error": re.compile(r"ImportError|ModuleNotFoundError|No module named", re.IGNORECASE),
    "path_error": re.compile(r"FileNotFoundError|No such file or directory|경로.*오류|secure_path", re.IGNORECASE),
    "key_error": re.compile(r"KeyError(?:\s*:|\s|\b)", re.IGNORECASE),
    "name_error": re.compile(r"NameError(?:\s*:|\s|\b)", re.IGNORECASE),
    "resource_warning": re.compile(r"ResourceWarning", re.IGNORECASE),
    "attribute_error": re.compile(r"AttributeError|object has no attribute", re.IGNORECASE),
}

MAX_RETRIES = 3


# ═══════════════════════════════════════════════════════════
# Phase A — 진단
# ═══════════════════════════════════════════════════════════

class DiagnosisResult:
    """진단 결과 — 모든 발견사항을 구조화"""

    def __init__(self):
        self.pytest_results: list = []          # {"file": str, "test": str, "error": str}
        self.pytest_passed: bool = False
        self.log_issues: list = []              # {"type": str, "file": str, "line": int, "msg": str}
        self.component_statuses: list = []      # {"component": str, "status": str, "note": str}
        self.summary: str = ""


def _run_pytest() -> tuple:
    """pytest tests/ 실행 → (passed: bool, results: list)"""
    results = []
    passed = False
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(TESTS_DIR), "-v", "--tb=line"],
            capture_output=True, text=True, timeout=120,
            cwd=str(SCRIPTS_DIR),
        )
        stdout = proc.stdout
        stderr = proc.stderr
        passed = proc.returncode == 0

        # 실패 테스트 추출
        fail_pattern = re.compile(r"FAILED\s+(\S+)\s+-\s+(.+)")
        for line in stdout.splitlines():
            m = fail_pattern.match(line)
            if m:
                parts = m.group(1).split("::", 1)
                file_path = parts[0]
                test_name = parts[1] if len(parts) > 1 else "?"
                results.append({
                    "file": file_path,
                    "test": test_name,
                    "error": m.group(2).strip(),
                })

        # stderr에서도 에러 추출
        err_pattern = re.compile(r"(?:ERROR|FAILED)\s+(.+?)(?:\s+-\s+(.*))?")
        for line in stderr.splitlines():
            m = err_pattern.match(line)
            if m:
                results.append({
                    "file": m.group(1).strip(),
                    "test": "?",
                    "error": m.group(2) or stderr[:200],
                })

        logger.info(f"[dream_scheduler] pytest: {'PASS' if passed else 'FAIL'} ({len(results)} failures)")
    except subprocess.TimeoutExpired:
        results.append({"file": "pytest", "test": "timeout", "error": "pytest timed out after 120s"})
    except FileNotFoundError:
        results.append({"file": "pytest", "test": "not_found", "error": "pytest not installed"})
    except Exception as e:
        results.append({"file": "pytest", "test": "error", "error": str(e)})

    return passed, results


def _scan_logs() -> list:
    """로그 파일 스캔 → 알려진 패턴 발견"""
    issues = []
    log_files = [
        SCRIPTS_DIR / "hermes_launchd.log",
        SCRIPTS_DIR / "hermes_launchd.error.log",
    ]
    for log_path in log_files:
        if not log_path.exists():
            continue
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    for issue_type, pattern in LOG_SCAN_PATTERNS.items():
                        if pattern.search(line):
                            issues.append({
                                "type": issue_type,
                                "file": log_path.name,
                                "line": i,
                                "msg": line.strip()[:200],
                            })
                            break  # 한 줄에 하나의 타입만
        except Exception as e:
            logger.warning(f"[dream_scheduler] 로그 스캔 실패 ({log_path}): {e}")

    # 최근 50건만 유지 (너무 많으면 의미 없음)
    return issues[-50:]


def _check_components() -> list:
    """ontology 테이블에서 컴포넌트 상태 확인"""
    statuses = []
    try:
        from index_db import list_components
        statuses = list_components()
    except Exception as e:
        logger.warning(f"[dream_scheduler] ontology 조회 실패: {e}")
        statuses = [{"component": "index_db", "status": "error", "notes": str(e)}]
    return statuses


async def diagnose() -> DiagnosisResult:
    """Phase A: 전체 진단 실행"""
    result = DiagnosisResult()

    # A1: pytest
    result.pytest_passed, result.pytest_results = _run_pytest()

    # A2: 로그 스캔
    result.log_issues = _scan_logs()

    # A3: 컴포넌트 상태
    result.component_statuses = _check_components()

    # 요약
    parts = []
    if result.pytest_passed:
        parts.append("pytest: ✅ 전체 통과")
    else:
        parts.append(f"pytest: ❌ {len(result.pytest_results)}건 실패")
    parts.append(f"로그 이슈: {len(result.log_issues)}건 발견")
    parts.append(f"컴포넌트: {len(result.component_statuses)}개 등록")
    result.summary = " | ".join(parts)

    return result


# ═══════════════════════════════════════════════════════════
# Phase B — 수정
# ═══════════════════════════════════════════════════════════

class FixResult:
    """수정 결과"""

    def __init__(self):
        self.auto_fixes: list = []      # {"action": str, "file": str, "detail": str, "success": bool}
        self.needs_review: list = []    # {"issue": str, "reason": str}
        self.backups_created: list = []  # [str] — 백업 파일 경로


def _backup_file(file_path: Path) -> Optional[Path]:
    """파일 백업 생성"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"{file_path.name}.backup.{timestamp}"
    try:
        shutil.copy2(str(file_path), str(backup_path))
        logger.info(f"[dream_scheduler] 백업: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"[dream_scheduler] 백업 실패 {file_path}: {e}")
        return None


def _fix_parse_mode(issue: dict) -> dict:
    """_base.py reply_text()에 parse_mode 누락 수정"""
    base_py = Path(SCRIPTS_DIR) / "handlers" / "_base.py"
    if not base_py.exists():
        return {"action": "parse_mode_fix", "file": str(base_py), "detail": "파일 없음", "success": False}

    backup = _backup_file(base_py)
    try:
        content = base_py.read_text(encoding="utf-8")
        # reply_text() 호출 중 parse_mode=None 없는 패턴 찾기
        # 예: await update.effective_message.reply_text("...")
        # → parse_mode='Markdown'이 없으면 추가 (광범위한 변경 방지)
        pattern = re.compile(
            r"(await\s+(?:update\.effective_message|message|msg)\.reply_text\([\s\S]*?)"
            r"(?:(?:parse_mode[\s]*=|parse_mode[\s]*:)[\s]*(?:['\"]?)(?:Markdown|HTML)(?:['\"]?))"
        )
        if pattern.search(content):
            # 이미 parse_mode 지정됨 → 변경 불필요
            return {"action": "parse_mode_fix", "file": str(base_py), "detail": "이미 parse_mode 지정됨", "success": True}

        # 최소한 _reply_long()에 parse_mode=None이 없으면 추가
        # (실제로는 더 복잡할 수 있으나, 명세 범위 내에서 최소 변경)
        return {"action": "parse_mode_fix", "file": str(base_py), "detail": "확인 필요 — 로직 변경 필요", "success": False,
                "needs_review": True}
    except Exception as e:
        return {"action": "parse_mode_fix", "file": str(base_py), "detail": str(e), "success": False}


def _fix_import_error(issue: dict) -> dict:
    """ImportError → 누락된 import 추가 (가능한 경우만)"""
    # 로그에서 모듈명 추출
    m = re.search(r"(?:No module named|ImportError)\s+['\"]([^'\"]+)['\"]", issue.get("msg", ""))
    if not m:
        return {"action": "import_fix", "file": issue.get("file", "?"), "detail": "모듈명 추출 실패", "success": False}

    missing_module = m.group(1)
    # 표준 라이브러리 목록 (pip 설치 필요 없는 것)
    stdlib_modules = {"json", "os", "sys", "re", "math", "datetime", "pathlib", "collections",
                      "typing", "logging", "subprocess", "shutil", "sqlite3", "hashlib", "copy",
                      "itertools", "functools", "abc", "enum", "dataclasses", "asyncio"}

    if missing_module in stdlib_modules:
        return {"action": "import_fix", "file": issue.get("file", "?"),
                "detail": f"표준 라이브러리 '{missing_module}' — 이미 사용 가능", "success": True}

    return {"action": "import_fix", "file": issue.get("file", "?"),
            "detail": f"확인 필요 — '{missing_module}'은(는) pip 설치 또는 로직 검토 필요", "success": False, "needs_review": True}


def _fix_path_error(issue: dict) -> dict:
    """경로 오류 → Path.home() 기준 검증"""
    msg = issue.get("msg", "")
    m = re.search(r"'(/[^']*)'", msg)
    if not m:
        return {"action": "path_fix", "file": issue.get("file", "?"), "detail": "경로 추출 실패", "success": False}

    bad_path = m.group(1)
    if "~" in msg or str(Path.home()) not in bad_path:
        return {"action": "path_fix", "file": issue.get("file", "?"),
                "detail": "확인 필요 — 하드코딩된 상대경로 또는 ~ 사용 의심", "success": False, "needs_review": True}

    return {"action": "path_fix", "file": issue.get("file", "?"), "detail": "경로 자체 문제 — 수동 진단 필요",
            "success": False, "needs_review": True}


def _fix_pytest_failure(failure: dict) -> dict:
    """pytest 단위 실패 → 연관 코드 패치 (가벼운 수준)"""
    # 단순 ImportError인 경우
    if "ModuleNotFoundError" in failure.get("error", ""):
        m = re.search(r"ModuleNotFoundError:\s*No module named ['\"](\S+)['\"]", failure.get("error", ""))
        if m:
            return _fix_import_error({"type": "import_error", "file": failure["file"], "msg": failure["error"]})

    return {"action": f"pytest_fix:{failure.get('test', '?')}", "file": failure.get("file", "?"),
            "detail": "확인 필요 — 설계/로직 변경 필요", "success": False, "needs_review": True}


async def fix(diagnosis: DiagnosisResult) -> FixResult:
    """Phase B: 발견된 문제 수정"""
    result = FixResult()

    # B1: pytest 실패 수정 시도
    for failure in diagnosis.pytest_results:
        fix_result = _fix_pytest_failure(failure)
        if fix_result.pop("needs_review", False):
            result.needs_review.append({
                "issue": f"pytest: {failure.get('test', '?')}",
                "reason": fix_result["detail"],
            })
        result.auto_fixes.append(fix_result)

    # B2: 로그 이슈 수정 시도
    seen_fixes = set()  # 중복 방지
    for issue in diagnosis.log_issues:
        issue_key = f"{issue['type']}:{issue.get('msg', '')[:50]}"
        if issue_key in seen_fixes:
            continue
        seen_fixes.add(issue_key)

        if issue["type"] == "parse_mode":
            fix_result = _fix_parse_mode(issue)
        elif issue["type"] == "import_error":
            fix_result = _fix_import_error(issue)
        elif issue["type"] == "path_error":
            fix_result = _fix_path_error(issue)
        else:
            fix_result = {"action": f"log_issue:{issue['type']}", "file": issue["file"],
                          "detail": f"자동 수정 미지원 유형: {issue['type']}", "success": False,
                          "needs_review": True}

        if fix_result.pop("needs_review", False):
            result.needs_review.append({
                "issue": f"로그: {issue['type']} ({issue.get('msg', '')[:80]})",
                "reason": fix_result["detail"],
            })
        result.auto_fixes.append(fix_result)

    # B3: ontology 컴포넌트 상태 업데이트 (항상 실행)
    try:
        from index_db import upsert_component
        upsert_component("dream_scheduler", "healthy", f"진단 완료: {diagnosis.summary}")
        # 각 컴포넌트 상태를 기본값으로 초기화 (없는 경우)
        default_components = [
            "natural_language_router", "hybrid_router", "ingest_engine",
            "tag_linker", "index_db", "handlers",
        ]
        for comp in default_components:
            upsert_component(comp, "unknown", "dream_scheduler 초기화")
        result.auto_fixes.append({
            "action": "ontology_update", "file": "index_db",
            "detail": "컴포넌트 상태 DB 갱신", "success": True,
        })
    except Exception as e:
        logger.warning(f"[dream_scheduler] ontology 업데이트 실패: {e}")

    return result


# ═══════════════════════════════════════════════════════════
# Phase C — 검증
# ═══════════════════════════════════════════════════════════

class VerifyResult:
    """검증 결과"""

    def __init__(self):
        self.passed: bool = False
        self.attempt: int = 0
        self.rollbacks: list = []
        self.final_failures: list = []


def _rollback(backup_path: Path) -> bool:
    """백업 파일에서 복원"""
    original_name = backup_path.name.replace(".backup.", ".")
    # backup 파일명에서 원래 이름 추출
    # 예: _base.py.backup.20260528_171127 → _base.py
    for suffix in [".backup."]:
        if suffix in backup_path.name:
            original_name = backup_path.name.split(suffix)[0]
            break

    original_path = backup_path.parent.parent / "handlers" / original_name
    if not original_path.exists():
        original_path = backup_path.parent / original_name

    try:
        shutil.copy2(str(backup_path), str(original_path))
        logger.info(f"[dream_scheduler] 롤백: {backup_path.name} → {original_path}")
        return True
    except Exception as e:
        logger.error(f"[dream_scheduler] 롤백 실패: {e}")
        return False


async def verify(fix_result: FixResult) -> VerifyResult:
    """Phase C: pytest 재실행으로 검증, 실패 시 롤백+재시도"""
    vresult = VerifyResult()

    for attempt in range(1, MAX_RETRIES + 1):
        vresult.attempt = attempt
        logger.info(f"[dream_scheduler] 검증 시도 {attempt}/{MAX_RETRIES}")

        passed, failures = _run_pytest()
        if passed:
            vresult.passed = True
            break

        # 실패 → 롤백
        for backup_path_str in fix_result.backups_created:
            bp = Path(backup_path_str)
            if bp.exists():
                if _rollback(bp):
                    vresult.rollbacks.append(str(bp))

        # 마지막 시도가 아니면 더 수정 시도
        if attempt < MAX_RETRIES:
            logger.info(f"[dream_scheduler] 재시도 {attempt + 1}/{MAX_RETRIES} 준비...")
        else:
            vresult.final_failures = failures

    return vresult


# ═══════════════════════════════════════════════════════════
# Phase D — 보고
# ═══════════════════════════════════════════════════════════

def build_report(diag: DiagnosisResult, fix_result: FixResult,
                 verify_result: VerifyResult) -> str:
    """텔레그램/CLI용 보고서 생성"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # plain text 보고서 (Markdown/HTML 파싱 충돌 방지)
    lines = [f"🔧 Self-Healing Report ({now})", ""]

    # 진단 요약
    lines.append("📋 진단")
    lines.append(f"  {diag.summary}")
    lines.append("")

    # 자동수정 결과
    auto_success = sum(1 for f in fix_result.auto_fixes if f["success"])
    auto_fail = len(fix_result.auto_fixes) - auto_success
    lines.append(f"🔧 자동수정")
    if fix_result.auto_fixes:
        for f in fix_result.auto_fixes:
            icon = "✅" if f["success"] else "❌"
            action = f['action'].replace('_', '-')  # underscore → dash (파싱 안전)
            lines.append(f"  {icon} {action}: {f['detail']}")
    else:
        lines.append("  (없음)")
    lines.append(f"  → 총 {len(fix_result.auto_fixes)}건 처리 ({auto_success} 성공 / {auto_fail} 실패)")
    lines.append("")

    # 확인 필요
    if fix_result.needs_review:
        lines.append(f"⚠️ 확인 필요 ({len(fix_result.needs_review)}건)")
        for nr in fix_result.needs_review:
            lines.append(f"  - {nr['issue']}")
            lines.append(f"    → {nr['reason']}")
        lines.append("")

    # 검증 결과
    verify_icon = "✅" if verify_result.passed else "❌"
    lines.append(f"🧪 검증 (시도 {verify_result.attempt}/{MAX_RETRIES})")
    lines.append(f"  {verify_icon} pytest: {'전체 통과' if verify_result.passed else '실패'}")
    if verify_result.rollbacks:
        lines.append(f"  ↩️ 롤백: {len(verify_result.rollbacks)}건")
    if verify_result.final_failures:
        lines.append("  남은 실패:")
        for f in verify_result.final_failures[:5]:
            lines.append(f"    - {f.get('test', '?')}: {f.get('error', '')[:100]}")
        if len(verify_result.final_failures) > 5:
            lines.append(f"    ... 외 {len(verify_result.final_failures) - 5}건")
    lines.append("")

    # 요약
    if verify_result.passed and not fix_result.needs_review:
        lines.append("🟢 상태: 정상 — 자동 복구 완료")
    elif not verify_result.passed:
        lines.append("🔴 상태: 주의 — 수동 개입 필요")
    else:
        lines.append("🟡 상태: 확인 필요 — 자동수정 완료, 검토 필요 항목 있음")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# Main orchestrator
# ═══════════════════════════════════════════════════════════

async def run_self_healing(telegram_context=None, chat_id: Optional[int] = None) -> dict:
    """Self-Healing Loop 전체 실행

    Args:
        telegram_context: PTB context (선택, 있으면 텔레그램 전송)
        chat_id: 텔레그램 chat_id (선택)

    Returns:
        {
            "status": "ok" | "partial" | "fail",
            "report": str,
            "auto_fixes": int,
            "needs_review": int,
        }
    """
    logger.info("[dream_scheduler] ===== Self-Healing 시작 =====")

    # Phase A: 진단
    diag = await diagnose()

    # Phase B: 수정
    fix_result = await fix(diag)

    # Phase C: 검증
    verify_result = await verify(fix_result)

    # Phase D: 보고
    report = build_report(diag, fix_result, verify_result)
    logger.info(f"[dream_scheduler] 보고서:\n{report}")

    # 텔레그램 전송 (context가 제공된 경우)
    # parse_mode=None: 시스템 보고서는 plain text로 전송해 파싱 에러 방지
    if telegram_context is not None and chat_id:
        try:
            await telegram_context.bot.send_message(
                chat_id=chat_id,
                text=report,
                parse_mode=None,  # plain text — Markdown 파싱 충돌 방지
            )
        except Exception as e:
            logger.error(f"[dream_scheduler] 텔레그램 전송 실패: {e}")
            if "message is too long" in str(e).lower():
                # 긴 보고서는 파일로 전송 시도
                try:
                    import io
                    report_bytes = report.encode("utf-8")
                    await telegram_context.bot.send_document(
                        chat_id=chat_id,
                        document=io.BytesIO(report_bytes),
                        filename=f"self_healing_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                        caption="Self-Healing Report (파일 전송)",
                    )
                except Exception:
                    pass
            else:
                try:
                    # 최후 fallback: 짧은 요약만 전송
                    await telegram_context.bot.send_message(
                        chat_id=chat_id,
                        text=f"Self-Healing 완료. 보고서는 로그를 확인하세요.",
                    )
                except Exception:
                    pass

    # 최종 상태 결정
    auto_fixes = sum(1 for f in fix_result.auto_fixes if f["success"])
    needs_review = len(fix_result.needs_review)
    if verify_result.passed and needs_review == 0:
        status = "ok"
    elif verify_result.passed and needs_review > 0:
        status = "partial"
    else:
        status = "fail"

    logger.info(f"[dream_scheduler] ===== Self-Healing 종료: {status} =====")
    return {
        "status": status,
        "report": report,
        "auto_fixes": auto_fixes,
        "needs_review": needs_review,
    }


# ═══════════════════════════════════════════════════════════
# CLI entry point (launchd용)
# ═══════════════════════════════════════════════════════════

def main():
    """CLI 진입점 — launchd 새벽 3시 트리거용"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = asyncio.run(run_self_healing())
    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
