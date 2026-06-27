"""
_meta.py — /claude_brief, /save_wiki, /wiki_lint 핸들러
========================================
기존:
  /claude_brief — 4대 메타 문서 브리핑 생성

🆕 추가:
  /save_wiki [경로] — 현재 대화 마지막 분석을 wiki 페이지로 저장
  /wiki_lint        — wiki 고아/오래된/깨진링크/빈 페이지 탐지

자동 스캔 항목 제외:
  ❌ constitution.local.md (세션 시작 시 자동 스캔)
  ❌ 01_hot.md (세션 시작 시 자동 스캔)

유지 항목:
  ✅ 05_시스템 상태.md
  ✅ 시스템_구조적_결함_분석.md
  ✅ 메모리_파일_명세서.md
  ✅ HERMES3_MASTER_DEVELOPMENT_GUIDE.md
"""
import os
import re
import logging
from pathlib import Path
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes
from handlers._base import safe_reply, safe_edit

logger = logging.getLogger('HermesOrchestrator')

# ============================================================
# 경로 상수
# ============================================================
META_DIR = Path("/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta")
OUTPUT_FILE = META_DIR / "claude_briefing.md"
WIKI_ROOT   = Path("/Users/bluesea/Applications/Mjobsidian/wiki")

_MAX_FILE_SIZE = 50 * 1024  # 50KB


# ============================================================
# 파일 읽기 헬퍼
# ============================================================
def _read_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[claude_brief] 파일 읽기 실패: {path} — {e}")
        return ""


# ============================================================
# 섹션별 추출 함수 (기존 유지)
# ============================================================
def _extract_system_status(text: str) -> str:
    lines = text.strip().split("\n")
    return "\n".join(lines[-25:])


def _extract_defects(text: str) -> str:
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if any(kw in stripped for kw in ["미해결", "⚠️", "🔴", "TODO", "[ ]"]):
            result.append(line)
    return "\n".join(result) if result else "미해결 항목 없음"


def _extract_memory_spec(text: str) -> str:
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if "L1" in stripped or "L2" in stripped or "L3" in stripped:
            result.append(line)
    return "\n".join(result[:12])


def _extract_dev_guide(text: str) -> str:
    lines = text.split("\n")
    result = []
    version_line = ""
    for line in lines:
        stripped = line.strip()
        if "버전" in stripped or ("v" in stripped.lower() and any(c.isdigit() for c in stripped)):
            version_line = line
        if any(kw in stripped for kw in ["진행 중", "예정", "미완료", "[ ]"]):
            result.append(line)
    output = ""
    if version_line:
        output += version_line + "\n"
    output += "\n".join(result[:15])
    return output


# ============================================================
# 브리핑 생성 (기존 유지)
# ============================================================
def _generate_briefing() -> tuple:
    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    sections = {}
    missing = []

    for key, filename, extractor in [
        ("system_status", "05_시스템 상태.md",                _extract_system_status),
        ("defects",       "시스템_구조적_결함_분석.md",        _extract_defects),
        ("memory_spec",   "메모리_파일_명세서.md",             _extract_memory_spec),
        ("dev_guide",     "HERMES3_MASTER_DEVELOPMENT_GUIDE.md", _extract_dev_guide),
    ]:
        text = _read_file(META_DIR / filename)
        if text:
            sections[key] = extractor(text)
        else:
            sections[key] = None
            missing.append(filename)

    success_count = 4 - len(missing)

    lines = [
        "# Hermes Claude Briefing",
        f"생성일시: {now}",
        "버전: Hermes v9.2",
        "",
        "---",
        "",
        "## 📌 참고",
        "이 파일은 즉시 참조 용도입니다.",
        "- **constitution.local.md** — 세션 시작 시 자동 스캔",
        "- **01_hot.md** — 세션 시작 시 자동 스캔",
        "",
        "## 🖥️ 현재 시스템 상태",
        sections.get("system_status") or "[05_시스템 상태.md 누락]",
        "",
        "## 🚨 미해결 버그/장애",
        sections.get("defects") or "[시스템_구조적_결함_분석.md 누락]",
        "",
        "## 🧠 메모리 현황",
        sections.get("memory_spec") or "[메모리_파일_명세서.md 누락]",
        "",
        "## 🗺️ 개발 로드맵 (진행 중)",
        sections.get("dev_guide") or "[HERMES3_MASTER_DEVELOPMENT_GUIDE.md 누락]",
        "",
        "---",
        "*이 파일은 /claude_brief 명령어로 자동 생성됩니다.*",
        "*Claude 새 대화 시작 시 이 파일을 첨부하세요.*",
    ]

    content = "\n".join(lines)
    if len(content.encode("utf-8")) > _MAX_FILE_SIZE:
        content = _truncate_to_size(content)

    return content, success_count, missing


def _truncate_to_size(content: str, max_bytes: int = _MAX_FILE_SIZE) -> str:
    sections_inner = content.split("\n## ")
    result = [sections_inner[0]]
    for sec in sections_inner[1:]:
        lines = sec.split("\n")
        if len(lines) > 10:
            sec_header = lines[:2]
            sec_body = lines[2:]
            non_empty = [l for l in sec_body if l.strip()]
            keep = non_empty[:5]
            result.append("\n".join(sec_header + keep))
        else:
            result.append(sec)
    truncated = "\n## ".join(result)
    if len(truncated.encode("utf-8")) > max_bytes:
        truncated = truncated[:int(len(truncated) * 0.6)]
    return truncated


# ============================================================
# 기존 핸들러
# ============================================================
async def cmd_claude_brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/claude_brief` — 4대 메타 문서 브리핑 생성"""
    content, success_count, missing = _generate_briefing()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(content, encoding="utf-8")

    file_size_kb = len(content.encode("utf-8")) / 1024

    if not missing:
        msg = (
            "📄 Claude 브리핑 파일 생성 완료\n\n"
            f"• 소스: 4대 메타 문서\n"
            f"• 출력: wiki/00_Meta/claude_briefing.md\n"
            f"• 크기: {file_size_kb:.1f}KB\n\n"
            "Claude 새 대화 시작 시 위 파일을 첨부하세요."
        )
    else:
        missing_names = ", ".join(missing)
        msg = (
            "⚠️ Claude 브리핑 생성 완료 (일부 누락)\n\n"
            f"• 정상 추출: {success_count}개 파일\n"
            f"• 누락: {missing_names}\n"
            f"• 출력: wiki/00_Meta/claude_briefing.md\n"
            f"• 크기: {file_size_kb:.1f}KB"
        )

    await safe_reply(update.message, msg)


# ============================================================
# 🆕 /save_wiki 핸들러
# ============================================================
async def cmd_save_wiki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    `/save_wiki [경로]` — 직전 대화 분석 결과를 wiki 페이지로 저장.

    사용법:
        /save_wiki                        → 자동 경로 (분석/YYYYMMDD_HHMMSS.md)
        /save_wiki 주식분석/RDW전략.md    → 지정 경로
        /save_wiki 주식분석/RDW전략.md --overwrite

    LLM이 SAVE 태그로 내용을 지정하는 경우:
        harness_agent.py의 SAVE 태그 처리 로직과 연계 가능.
        context.user_data["last_save_content"] 에 내용이 있으면 사용.
    """
    from modules.wiki_manager import WikiManager  # 런타임 임포트 (순환 방지)

    args = context.args or []
    overwrite = "--overwrite" in args
    path_args = [a for a in args if not a.startswith("--")]

    # 경로 결정
    if path_args:
        rel_path = path_args[0]
        # .md 확장자 없으면 추가
        if not rel_path.endswith(".md"):
            rel_path += ".md"
    else:
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        rel_path = f"분석/{now_str}.md"

    # 저장할 내용 결정
    # 1순위: context.user_data에 LLM이 남긴 내용
    # 2순위: 직전 봇 메시지 (chat history에서 가져오기 어려워 안내만)
    content = context.user_data.get("last_save_content", "")

    if not content:
        # 내용이 없으면 사용법 안내
        await safe_reply(
            update.message,
            "📝 *저장할 내용이 없습니다.*\n\n"
            "사용법:\n"
            "1️⃣ LLM에게 분석 요청 후 `[SAVE]내용[/SAVE]` 태그로 감싸달라고 하세요.\n"
            "2️⃣ 또는 텍스트를 직접 입력 후 `/save_wiki 경로` 실행:\n"
            "`/save_wiki 주식분석/RDW분석.md`\n\n"
            "LLM이 분석한 내용에 SAVE 태그가 포함되면 자동으로 저장됩니다."
        )
        return

    wm = WikiManager()
    result = wm.write_wiki(rel_path, content, overwrite=overwrite)

    if result["ok"]:
        # 저장 후 context 초기화
        context.user_data.pop("last_save_content", None)
        size_kb = len(content.encode("utf-8")) / 1024
        await safe_reply(
            update.message,
            f"✅ *Wiki 저장 완료*\n\n"
            f"• 경로: `wiki/{rel_path}`\n"
            f"• 크기: {size_kb:.1f}KB\n"
            f"• index.md 자동 갱신 완료\n\n"
            f"Obsidian에서 바로 확인 가능합니다."
        )
    else:
        await safe_reply(update.message, result["msg"])


# ============================================================
# 🆕 /wiki_lint 핸들러
# ============================================================
async def cmd_wiki_lint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    `/wiki_lint [일수]` — wiki 건강 상태 점검.

    사용법:
        /wiki_lint        → 30일 기준
        /wiki_lint 60     → 60일 기준 stale 탐지
    """
    from modules.wiki_manager import WikiManager

    args = context.args or []
    stale_days = 30
    if args:
        try:
            stale_days = int(args[0])
        except ValueError:
            pass

    await safe_reply(update.message, f"🔍 Wiki Lint 실행 중... (stale 기준: {stale_days}일)")

    wm = WikiManager()
    try:
        result = wm.lint_wiki(stale_days=stale_days)
    except Exception as e:
        logger.error(f"[wiki_lint] 오류: {e}", exc_info=True)
        await safe_reply(update.message, f"❌ Lint 실행 오류: {e}")
        return

    await safe_reply(update.message, result["summary"])
