"""harness_verifier.py — Hermes 구조 건강 진단 (2026-06-09)

/verify_harness 명령어의 백엔드.
AI 의견이 아닌 실제 파일·프로세스·수치를 측정해 점수화한다.

6개 항목 측정:
  1. 파일 비대화 (줄 수 기반)
  2. 모듈 의존성 (import 수 기반 SRP 위반 감지)
  3. 문서 동기화 (02_스크립트 정보.md ↔ 실제 파일)  ← 문서 표류 방지
  4. 서비스 상태 (launchctl)
  5. 메모리 파일 건강 (JSON 파싱 + 크기)
  6. 임시 파일 잔재 (.bak / _old / _test)
"""

import os
import re
import json
import subprocess
from pathlib import Path
from typing import List, Tuple

# ── 경로 상수 ───────────────────────────────────────────────
SCRIPTS_DIR = Path("/Users/bluesea/Applications/Mjauto/Scripts")
MODULES_DIR = SCRIPTS_DIR / "modules"
HANDLERS_DIR = SCRIPTS_DIR / "handlers"
META_DIR = Path("/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta")
DOC_FILE = META_DIR / "02_스크립트 정보.md"

MEMORY_FILES = {
    "L1 harness_memory": SCRIPTS_DIR / "harness_memory.json",
    "L2 episodic_memory": Path("/Users/bluesea/.hermes/runtime/memory/episodic_memory.json"),
    "L3 semantic_memory": Path("/Users/bluesea/.hermes/runtime/memory/semantic_memory.json"),
}

LAUNCHD_SERVICES = {
    "com.hermes.bot":     "Hermes1 봇",
    "ai.hermes2.bot":     "Hermes2 봇",
    "ai.hermes.gateway":  "Gateway",
}

# 파일 크기 임계값 (줄 수)
LINE_OK      = 300
LINE_CAUTION = 600
LINE_WARN    = 900

# SRP 위반 임계값 (from/import modules 수)
IMPORT_WARN = 10

# 문서화 면제 패턴 (이런 파일은 미문서화여도 정상)
DOC_EXEMPT = {"__init__", "test_", "_run_", "migrate_", "setup_", "update_index"}

# 문서에 언급되지만 Scripts/ 외부에 위치한 파일 (venu 패키지, 외부 경로)
DOC_EXTERNAL = {"api_server.py", "run.py"}


# ── 1. 파일 비대화 감사 ─────────────────────────────────────

def check_file_sizes() -> Tuple[int, List[str]]:
    """Scripts 내 .py 파일 크기 검사. (점수, 메시지 목록) 반환."""
    issues = []
    score = 20  # 만점

    targets = (
        list(SCRIPTS_DIR.glob("*.py")) +
        list(MODULES_DIR.rglob("*.py")) +
        list(HANDLERS_DIR.glob("*.py"))
    )

    red, yellow, caution = [], [], []
    for f in targets:
        if "__pycache__" in str(f):
            continue
        lines = len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
        if lines >= LINE_WARN:
            red.append((f.name, lines))
        elif lines >= LINE_WARN - 300:  # 600+
            yellow.append((f.name, lines))
        elif lines >= LINE_OK:
            caution.append((f.name, lines))

    for name, n in sorted(red, key=lambda x: -x[1]):
        issues.append(f"🔴 {name} — {n}줄 (위험: 900+, SRP 분할 권장)")
        score -= 4
    for name, n in sorted(yellow, key=lambda x: -x[1]):
        issues.append(f"🟡 {name} — {n}줄 (경고: 600+)")
        score -= 1
    for name, n in caution[:3]:  # 주의는 상위 3개만
        issues.append(f"🟢 {name} — {n}줄 (주의: 300+)")

    if not red and not yellow:
        issues.append("✅ 모든 파일 600줄 이하 — 양호")

    return max(0, score), issues


# ── 2. 모듈 의존성 (SRP 위반 감지) ─────────────────────────

def check_imports() -> Tuple[int, List[str]]:
    """핵심 파일의 from modules. import 수 검사."""
    issues = []
    score = 15

    key_files = [
        SCRIPTS_DIR / "harness_agent.py",
        SCRIPTS_DIR / "hermes_local.py",
    ]

    for f in key_files:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        count = len(re.findall(r'^(?:from modules\.|import modules\.)', text, re.MULTILINE))
        if count > IMPORT_WARN:
            issues.append(f"⚠️ {f.name} — modules 직접 import {count}개 (임계 {IMPORT_WARN}개 초과, 파사드 레이어 도입 권장)")
            score -= 5
        else:
            issues.append(f"✅ {f.name} — modules import {count}개 (정상)")

    return max(0, score), issues


# ── 3. 문서 동기화 (표류 감지) ──────────────────────────────

def check_doc_sync() -> Tuple[int, List[str]]:
    """02_스크립트 정보.md에 언급된 .py 파일이 실제로 존재하는지 확인.
    또한 Scripts/modules/ 내 신규 .py 중 문서에 없는 것도 보고.
    이것이 '문서 표류 방지' 자동화의 핵심.
    """
    issues = []
    score = 25

    if not DOC_FILE.exists():
        return 0, ["❌ 02_스크립트 정보.md 파일을 찾을 수 없음"]

    doc_text = DOC_FILE.read_text(encoding="utf-8", errors="ignore")

    # 문서에 언급된 .py 파일명 추출 (backtick 또는 일반 텍스트)
    mentioned = set(re.findall(r'`?([\w_\-]+\.py)`?', doc_text))

    # 실제 존재하는 모든 .py 파일 (서브폴더 포함)
    all_py = set()
    for f in (
        list(SCRIPTS_DIR.glob("*.py")) +
        list(MODULES_DIR.rglob("*.py")) +
        list(HANDLERS_DIR.rglob("*.py")) +
        list((SCRIPTS_DIR / "tests").rglob("*.py")) +
        list((SCRIPTS_DIR / "_archive").rglob("*.py"))
    ):
        if "__pycache__" not in str(f):
            all_py.add(f.name)

    # 문서에 있는데 실제 없는 파일 (→ 표류된 문서)
    ghost = [f for f in mentioned if f not in all_py
             and not any(f.startswith(e) for e in DOC_EXEMPT)
             and f not in DOC_EXTERNAL]
    # 실제 있는데 문서에 없는 파일 (→ 미문서화 신규 파일)
    undoc = [f for f in all_py if f not in mentioned
             and not any(f.startswith(e) for e in DOC_EXEMPT)
             and not f.startswith("_")]

    if ghost:
        for f in sorted(ghost)[:5]:
            issues.append(f"👻 문서에 있지만 파일 없음: `{f}` — 문서 표류!")
        score -= len(ghost) * 3
    else:
        issues.append("✅ 문서 언급 파일 전부 실존")

    if undoc:
        for f in sorted(undoc)[:5]:
            issues.append(f"📄 미문서화 신규 파일: `{f}` — 02_스크립트 정보.md 업데이트 필요")
        score -= len(undoc) * 2
    else:
        issues.append("✅ 신규 미문서화 파일 없음")

    return max(0, score), issues


# ── 4. 서비스 상태 ───────────────────────────────────────────

def check_services() -> Tuple[int, List[str]]:
    """launchctl로 핵심 서비스 실행 여부 확인."""
    issues = []
    score = 20

    for label, name in LAUNCHD_SERVICES.items():
        try:
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # PID 추출
                pid_match = re.search(r'"PID"\s*=\s*(\d+)', result.stdout)
                pid = pid_match.group(1) if pid_match else "?"
                issues.append(f"✅ {name} ({label}) — PID {pid} 실행중")
            else:
                issues.append(f"❌ {name} ({label}) — 미실행")
                score -= 6
        except Exception as e:
            issues.append(f"⚠️ {name} 확인 실패: {e}")
            score -= 3

    return max(0, score), issues


# ── 5. 메모리 파일 건강 ──────────────────────────────────────

def check_memory() -> Tuple[int, List[str]]:
    """L1/L2/L3 메모리 JSON 파일 존재 + 파싱 가능 여부 확인."""
    issues = []
    score = 10

    for name, path in MEMORY_FILES.items():
        if not path.exists():
            issues.append(f"⚠️ {name} — 파일 없음 (미생성 or 삭제됨)")
            score -= 2
            continue
        size = path.stat().st_size
        size_str = f"{size // 1024}KB" if size > 1024 else f"{size}B"
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
            issues.append(f"✅ {name} — {size_str} (정상)")
        except json.JSONDecodeError:
            issues.append(f"❌ {name} — {size_str} JSON 파싱 실패! 손상 가능성")
            score -= 4

    return max(0, score), issues


# ── 6. 임시 파일 잔재 ───────────────────────────────────────

def check_temp_files() -> Tuple[int, List[str]]:
    """.bak / _old / _test 임시 파일 탐지."""
    issues = []
    score = 10

    bak_patterns = ["*.bak*", "*_old*", "*_backup*"]
    found = []

    search_roots = [SCRIPTS_DIR, MODULES_DIR, HANDLERS_DIR]
    for root in search_roots:
        for pattern in bak_patterns:
            for f in root.glob(pattern):
                if "__pycache__" not in str(f) and f.is_file():  # 폴더 제외
                    found.append(f.relative_to(SCRIPTS_DIR))

    if found:
        for f in found[:5]:
            issues.append(f"🗑️ 임시 파일 잔재: `{f}` — 삭제 권장")
        score -= len(found) * 3
    else:
        issues.append("✅ 임시 파일(.bak/_old) 없음")

    return max(0, score), issues


# ── 등급 판정 헬퍼 ───────────────────────────────────────────

def _grade(score: int) -> str:
    if score >= 90: return "S"
    if score >= 75: return "A"
    if score >= 60: return "B"
    if score >= 45: return "C"
    return "D"

def _grade_label(score: int) -> str:
    g = _grade(score)
    labels = {
        "S": "🟢 S — 매우 양호",
        "A": "🟢 A — 양호",
        "B": "🟡 B — 보통 (일부 개선 권장)",
        "C": "🟠 C — 주의 (적극 개선 권장)",
        "D": "🔴 D — 위험 (즉시 조치 필요)",
    }
    return labels[g]


# ── 종합 리포트 생성 ─────────────────────────────────────────

REPORT_MD_PATH = META_DIR / "verify_harness_report.md"

SECTIONS = [
    ("📏 파일 비대화",        check_file_sizes),
    ("🔗 모듈 의존성 (SRP)",  check_imports),
    ("📚 문서 동기화",        check_doc_sync),
    ("⚡ 서비스 상태",        check_services),
    ("🧠 메모리 파일",        check_memory),
    ("🗑️ 임시 파일",          check_temp_files),
]


def run_full_check() -> str:
    """6개 항목 전체 검사. 텔레그램용 HTML 문자열 반환. MD 파일에도 자동 저장."""
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    total_score = 0
    section_results = []   # (title, score, msgs) 구조화 보관

    for title, fn in SECTIONS:
        try:
            score, msgs = fn()
        except Exception as e:
            score, msgs = 0, [f"❌ 검사 실패: {e}"]
        total_score += score
        section_results.append((title, score, msgs))

    grade_str = _grade_label(total_score)

    # ── 텔레그램용 HTML ─────────────────────────────────────
    html_lines = [f"🏥 <b>Hermes 구조 진단 리포트</b>", f"<i>생성: {now}</i>", ""]
    for title, score, msgs in section_results:
        html_lines.append(f"<b>{title}</b>")
        for m in msgs:
            html_lines.append(f"  {m}")
        html_lines.append("")
    html_lines.append(f"<b>📊 종합 점수: {total_score}/100 — {grade_str}</b>")
    html_lines.append("")
    html_lines.append("<i>💡 분할 필요 파일은 별도 Claude Code 세션에서 진행 권장</i>")
    html_lines.append("<i>💡 문서 표류 항목은 /claude_brief 후 Claude Code 세션에서 정리</i>")
    html_report = "\n".join(html_lines)

    # ── MD 파일 저장 (자동 호출) ─────────────────────────────
    try:
        _save_report_md(now, total_score, section_results)
    except Exception as e:
        print(f"⚠️ [Verifier] MD 저장 실패 (무시): {e}")

    return html_report


def _save_report_md(now: str, total_score: int, section_results: list):
    """진단 결과를 verify_harness_report.md에 저장.

    구조:
      - 헤더 + 현재 상태 (매 실행 시 덮어씌움)
      - 📈 진단 이력 (최근 20회 보존, 오래된 것 자동 삭제)
    """
    import re as _re

    grade_str = _grade_label(total_score)
    grade_char = _grade(total_score)

    # ── 현재 상태 섹션 (plain text) ──────────────────────────
    plain_lines = []
    red_count, ghost_count, undoc_count = 0, 0, 0
    svc_ok, svc_total = 0, len(LAUNCHD_SERVICES)

    for title, score, msgs in section_results:
        plain_lines.append(f"### {title}")
        for m in msgs:
            plain_lines.append(f"- {m}")
            # 요약 집계용 카운팅
            if "🔴" in m: red_count += 1
            if "👻" in m: ghost_count += 1
            if "📄 미문서화" in m: undoc_count += 1
            if "✅" in m and "실행중" in m: svc_ok += 1
        plain_lines.append("")

    current_section = "\n".join(plain_lines)

    # ── 이력 행 생성 ─────────────────────────────────────────
    issues_summary = []
    if red_count:   issues_summary.append(f"🔴{red_count}개")
    if ghost_count: issues_summary.append(f"👻{ghost_count}개")
    if undoc_count: issues_summary.append(f"📄{undoc_count}개")
    issues_str = " ".join(issues_summary) if issues_summary else "없음"

    new_row = (
        f"| {now} | {total_score}/100 | {grade_char} | "
        f"{issues_str} | {svc_ok}/{svc_total} |"
    )

    # ── 기존 파일에서 이력 추출 ──────────────────────────────
    history_rows = []
    if REPORT_MD_PATH.exists():
        old_text = REPORT_MD_PATH.read_text(encoding="utf-8")
        # 이력 테이블 행 추출 (헤더/구분선 제외)
        for line in old_text.splitlines():
            if line.startswith("| ") and "날짜" not in line and "---" not in line:
                history_rows.append(line)

    # 최신 행을 맨 위에 추가, 최대 20개 보존
    history_rows = [new_row] + history_rows
    history_rows = history_rows[:20]
    history_table = "\n".join([
        "| 날짜 | 점수 | 등급 | 주요 이슈 | 서비스 |",
        "|---|---|---|---|---|",
    ] + history_rows)

    # ── 전체 MD 파일 구성 ────────────────────────────────────
    md_content = f"""# Hermes 구조 진단 리포트
> 자동 생성 — `/verify_harness` 실행 시마다 현재 상태 덮어씌움
> 최종 진단: {now} | 점수: **{total_score}/100** | 등급: **{grade_char}**
> 이력: 최근 20회 자동 보존 (오래된 항목 자동 삭제)

---

## 현재 상태 ({now})

**📊 종합 점수: {total_score}/100 — {grade_str}**

{current_section}
---

## 📈 진단 이력 (최근 20회)

{history_table}

---
*관련: `modules/harness_verifier.py` | 텔레그램: `/verify_harness` | 연관: `02_스크립트 정보.md`*
"""

    REPORT_MD_PATH.write_text(md_content, encoding="utf-8")
    print(f"✅ [Verifier] 리포트 저장: {REPORT_MD_PATH}")
