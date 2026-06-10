#!/usr/bin/env python3
"""
update_index.py — Live Sync: 파일 변경 시 hermes_index.db 실시간 갱신 v1.0
=======================================================================
CLI: python3 update_index.py --path /변경된/파일/경로

파일 확장자/경로로 테이블 결정:
  - .md 파일 (Meta, governance, AI_Automation)
    → tags 테이블 (#태그 파싱 + UPSERT)
    → ontology 테이블 (문서 메타 갱신)
  - handlers/*.py, modules/*.py
    → ontology 테이블 (컴포넌트 상태 동기화)

사용: fswatch -0 <paths> | xargs -0 -n1 python3 update_index.py --path
"""

import argparse
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger('HermesOrchestrator')

# ── 상수 ──────────────────────────────────────────────────
SCRIPTS_DIR = Path.home() / "Applications" / "Mjauto" / "Scripts"
DB_PATH = Path.home() / ".hermes" / "runtime" / "hermes_index.db"

# 감시 대상 파일 패턴
META_DIRS = [
    Path.home() / "Applications" / "Mjobsidian" / "wiki" / "00_Meta",
]
GOVERNANCE_DIR = Path.home() / ".hermes" / "governance"
HANDLERS_DIR = SCRIPTS_DIR / "handlers"
MODULES_DIR = SCRIPTS_DIR / "modules"
AI_AUTOMATION_DIR = Path.home() / "Applications" / "Mjobsidian" / "wiki" / "10_AI_Automation"


# ── 코어 함수 ─────────────────────────────────────────────

def _get_tag_db():
    """index_db 모듈 임포트 (lazy)"""
    sys.path.insert(0, str(SCRIPTS_DIR))
    sys.path.insert(0, str(MODULES_DIR))
    from index_db import upsert_component as _upsert
    return _upsert


def _parse_tags(content: str) -> list:
    """Markdown 내용에서 #태그 추출"""
    # #태그 패턴 (## 제목, # 숫자 제외)
    tags = set()
    for m in re.finditer(r'(?:^|\s)(#[A-Za-z가-힣][A-Za-z가-힣0-9_\-/]*)(?:\s|$)', content):
        tag = m.group(1).strip()
        # heading/주석 제외
        if not tag.startswith("##") and not tag.startswith("# "):
            tags.add(tag)
    return sorted(tags)


def _get_component_name(file_path: Path) -> str:
    """파일 경로 → ontology 컴포넌트명"""
    if "handlers" in file_path.parts:
        return f"handlers/{file_path.stem}"
    elif "modules" in file_path.parts:
        return file_path.stem
    elif "governance" in file_path.parts:
        return f"governance/{file_path.stem}"
    elif "00_Meta" in file_path.parts:
        return f"meta/{file_path.stem}"
    elif "10_AI_Automation" in file_path.parts:
        return f"ai_auto/{file_path.stem}"
    return file_path.stem


def update_index(file_path: str) -> dict:
    """변경된 파일 경로를 받아 DB 갱신

    Returns:
        {"status": "ok"|"skip"|"error", "table": str, "detail": str}
    """
    path = Path(file_path)

    # 존재하지 않거나 디렉토리면 skip
    if not path.exists() or path.is_dir():
        return {"status": "skip", "table": "", "detail": "존재하지 않거나 디렉토리"}

    # 확장자 체크
    if path.suffix not in (".md", ".py"):
        return {"status": "skip", "table": "", "detail": f"미지원 확장자: {path.suffix}"}

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"status": "error", "table": "", "detail": f"읽기 실패: {e}"}

    try:
        from index_db import get_conn
        from index_db import upsert_component
    except ImportError:
        sys.path.insert(0, str(SCRIPTS_DIR))
        sys.path.insert(0, str(MODULES_DIR))
        from index_db import get_conn
        from index_db import upsert_component

    result = {"status": "ok", "table": "", "detail": ""}

    # ── 1. tags 테이블 (.md 파일) ─────────────────────
    if path.suffix == ".md":
        tags = _parse_tags(content)
        if tags:
            with get_conn() as conn:
                # 기존 태그 정리 후 재등록 (파일 경로 기반 정리는 생략 — tag_linker가 담당)
                for tag in tags:
                    conn.execute(
                        """INSERT OR IGNORE INTO tags (tag, category, updated_at)
                           VALUES (?, 'live_sync', datetime('now'))""",
                        (tag,)
                    )
            result["table"] = "tags"
            result["detail"] = f"태그 {len(tags)}개 동기화"

        # ontology에도 문서 메타 등록
        component = _get_component_name(path)
        from index_db import upsert_component
        upsert_component(
            component,
            "synced",
            f"LiveSync: {path.name} ({path.parent.name})"
        )
        result["table"] += f"{' → ' if result['table'] else ''}ontology"
        result["detail"] = f"{result['detail']}, 컴포넌트 '{component}' 갱신"

    # ── 2. ontology 테이블 (.py → handlers/modules) ─────
    elif path.suffix == ".py":
        component = _get_component_name(path)
        if component:
            upsert_component(
                component,
                "healthy",
                f"LiveSync: 코드 변경 감지 ({path.name})"
            )
            result["table"] = "ontology"
            result["detail"] = f"컴포넌트 '{component}' → healthy"

        # 해당 컴포넌트 스크립트의 import 체크 (단순 존재 확인)
        if "ImportError" in content or "ModuleNotFoundError" in content or "No module named" in content:
            upsert_component(component, "warning", f"import 오류 의심: {path.name}")
            result["detail"] += " (import 경고)"

    return result


# ── CLI ───────────────────────────────────────────────────

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
        ],
    )


def main():
    parser = argparse.ArgumentParser(
        description="Live Sync: 파일 변경 시 hermes_index.db 실시간 갱신"
    )
    parser.add_argument(
        "--path", "-p",
        required=True,
        help="변경된 파일의 절대 경로",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="상세 로그 출력",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    result = update_index(args.path)
    logger.info(f"[update_index] {result['status']} | {result.get('table','')} | {result.get('detail','')}")

    # stderr 외에 stdout에도 결과 출력 (fswatch 체인용)
    if result["status"] != "skip":
        print(f"{result['status']}: {result.get('table','')} — {result.get('detail','')}", file=sys.stderr)
    sys.exit(0 if result["status"] != "error" else 1)


if __name__ == "__main__":
    main()
