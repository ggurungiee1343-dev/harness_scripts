"""
_meta.py — /claude_brief 명령어 핸들러 (간소화)
========================================
4대 메타 문서에서 필요한 정보만 추출하여 claude_briefing.md 생성 후 텔레그램 전송.

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
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger('HermesOrchestrator')

# ============================================================
# 경로 상수 (간소화됨)
# ============================================================
META_DIR = Path("/Users/bluesea/Applications/Mjobsidian/wiki/00_Meta")
OUTPUT_FILE = META_DIR / "claude_briefing.md"

_MAX_FILE_SIZE = 50 * 1024  # 50KB


# ============================================================
# 파일 읽기 헬퍼
# ============================================================
def _read_file(path: Path) -> str:
    """파일 읽기, 없으면 빈 문자열"""
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[claude_brief] 파일 읽기 실패: {path} — {e}")
        return ""


# ============================================================
# 섹션별 추출 함수
# ============================================================
def _extract_system_status(text: str) -> str:
    """05_시스템 상태.md → 최신 25줄"""
    lines = text.strip().split("\n")
    return "\n".join(lines[-25:])


def _extract_defects(text: str) -> str:
    """시스템_구조적_결함_분석.md → 미해결 항목만"""
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if any(kw in stripped for kw in ["미해결", "⚠️", "🔴", "TODO", "[ ]"]):
            result.append(line)
    return "\n".join(result) if result else "미해결 항목 없음"


def _extract_memory_spec(text: str) -> str:
    """메모리_파일_명세서.md → L1/L2/L3 라인만, 최대 12줄"""
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if "L1" in stripped or "L2" in stripped or "L3" in stripped:
            result.append(line)
    return "\n".join(result[:12])


def _extract_dev_guide(text: str) -> str:
    """HERMES3_MASTER_DEVELOPMENT_GUIDE.md → 버전 + 진행 중 항목, 최대 15줄"""
    lines = text.split("\n")
    result = []
    version_line = ""
    for line in lines:
        stripped = line.strip()
        # 버전 번호 라인
        if "버전" in stripped or ("v" in stripped.lower() and any(c.isdigit() for c in stripped)):
            version_line = line
        # 진행 중, 예정, 미완료, [ ]
        if any(kw in stripped for kw in ["진행 중", "예정", "미완료", "[ ]"]):
            result.append(line)

    output = ""
    if version_line:
        output += version_line + "\n"
    output += "\n".join(result[:15])
    return output


# ============================================================
# 브리핑 생성 (간소화됨)
# ============================================================
def _generate_briefing() -> tuple:
    """
    claude_briefing.md 생성 (4개 파일만)
    Returns: (content: str, success_count: int, missing: list)
    """
    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")

    sections = {}
    missing = []

    # 1. 05_시스템 상태.md
    path = META_DIR / "05_시스템 상태.md"
    text = _read_file(path)
    if text:
        sections["system_status"] = _extract_system_status(text)
    else:
        sections["system_status"] = None
        missing.append("05_시스템 상태.md")

    # 2. 시스템_구조적_결함_분석.md
    path = META_DIR / "시스템_구조적_결함_분석.md"
    text = _read_file(path)
    if text:
        sections["defects"] = _extract_defects(text)
    else:
        sections["defects"] = None
        missing.append("시스템_구조적_결함_분석.md")

    # 3. 메모리_파일_명세서.md
    path = META_DIR / "메모리_파일_명세서.md"
    text = _read_file(path)
    if text:
        sections["memory_spec"] = _extract_memory_spec(text)
    else:
        sections["memory_spec"] = None
        missing.append("메모리_파일_명세서.md")

    # 4. HERMES3_MASTER_DEVELOPMENT_GUIDE.md
    path = META_DIR / "HERMES3_MASTER_DEVELOPMENT_GUIDE.md"
    text = _read_file(path)
    if text:
        sections["dev_guide"] = _extract_dev_guide(text)
    else:
        sections["dev_guide"] = None
        missing.append("HERMES3_MASTER_DEVELOPMENT_GUIDE.md")

    success_count = 4 - len(missing)

    # ── 출력 파일 조립 ──
    lines = []
    lines.append("# Hermes Claude Briefing")
    lines.append(f"생성일시: {now}")
    lines.append(f"버전: Hermes v9.2")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 참고 사항
    lines.append("## 📌 참고")
    lines.append("이 파일은 즉시 참조 용도입니다.")
    lines.append("- **constitution.local.md** — 세션 시작 시 자동 스캔")
    lines.append("- **01_hot.md** — 세션 시작 시 자동 스캔")
    lines.append("")

    # 현재 시스템 상태
    lines.append("## 🖥️ 현재 시스템 상태")
    lines.append(sections.get("system_status") or "[05_시스템 상태.md 누락]")
    lines.append("")

    # 미해결 버그/장애
    lines.append("## 🚨 미해결 버그/장애")
    lines.append(sections.get("defects") or "[시스템_구조적_결함_분석.md 누락]")
    lines.append("")

    # 메모리 현황
    lines.append("## 🧠 메모리 현황")
    lines.append(sections.get("memory_spec") or "[메모리_파일_명세서.md 누락]")
    lines.append("")

    # 개발 로드맵
    lines.append("## 🗺️ 개발 로드맵 (진행 중)")
    lines.append(sections.get("dev_guide") or "[HERMES3_MASTER_DEVELOPMENT_GUIDE.md 누락]")
    lines.append("")

    lines.append("---")
    lines.append("*이 파일은 /claude_brief 명령어로 자동 생성됩니다.*")
    lines.append("*Claude 새 대화 시작 시 이 파일을 첨부하세요.*")

    content = "\n".join(lines)

    # 50KB 제한: 초과 시 간소화
    if len(content.encode("utf-8")) > _MAX_FILE_SIZE:
        content = _truncate_to_size(content)

    return content, success_count, missing


def _truncate_to_size(content: str, max_bytes: int = _MAX_FILE_SIZE) -> str:
    """50KB 초과 시 각 섹션을 균등하게 축소"""
    sections_inner = content.split("\n## ")
    result = [sections_inner[0]]
    for sec in sections_inner[1:]:
        lines = sec.split("\n")
        if len(lines) > 10:
            # 요약: 처음 2줄(헤더) + 핵심 5줄
            sec_header = lines[:2]
            sec_body = lines[2:]
            non_empty = [l for l in sec_body if l.strip()]
            keep = non_empty[:5]
            result.append("\n".join(sec_header + keep))
        else:
            result.append(sec)

    truncated = "\n## ".join(result)
    # 그래도 초과하면 더 축소
    if len(truncated.encode("utf-8")) > max_bytes:
        truncated = truncated[:int(len(truncated) * 0.6)]
    return truncated


# ============================================================
# 텔레그램 핸들러
# ============================================================
async def cmd_claude_brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/claude_brief` — 4대 메타 문서 브리핑 생성"""
    # 승인 확인 없이 바로 실행 (읽기 전용 작업)

    content, success_count, missing = _generate_briefing()

    # 파일 쓰기
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

    await update.message.reply_text(msg)
