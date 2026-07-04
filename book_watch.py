#!/usr/bin/env python3
"""
Clippings 폴더에 책 분량 PDF가 들어오면 텔레그램으로 알림.
book-to-skill(~/.claude/skills/book-to-skill/)은 LLM 에이전트가 직접
읽고 구조화하는 Claude Code 스킬이라 완전 무인 자동화는 불가 — 이 스크립트는
"책 발견 → 알림" 까지만 하고, 실제 변환은 Claude Code 세션에서
`/book-to-skill <경로>` 로 사람이 트리거한다.

트리거: launchd WatchPaths(Clippings/) — 2026-07-02 book_watch.plist
"""
import json
import sys
import subprocess
from pathlib import Path

import fitz  # PyMuPDF — ingest_text_utils.py와 동일 의존성

CLIPPINGS_DIR = Path("/Users/bluesea/Applications/Mjobsidian/Clippings")
STATE_FILE = Path.home() / ".hermes" / "runtime" / "book_watch_notified.json"
SEND_MSG = Path.home() / "Applications" / "Mjauto" / "Scripts" / "send_telegram_msg.py"
BOOK_PAGE_THRESHOLD = 50


def load_notified() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_notified(notified: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(notified), ensure_ascii=False, indent=2))


def main() -> None:
    if not CLIPPINGS_DIR.exists():
        print(f"Clippings 폴더 없음: {CLIPPINGS_DIR}", file=sys.stderr)
        return

    notified = load_notified()
    changed = False

    for pdf_path in CLIPPINGS_DIR.glob("*.pdf"):
        key = pdf_path.name
        if key in notified:
            continue

        try:
            doc = fitz.open(pdf_path)
            page_count = len(doc)
            doc.close()
        except Exception as e:
            print(f"페이지 수 확인 실패, 건너뜀: {pdf_path.name} ({e})", file=sys.stderr)
            notified.add(key)
            changed = True
            continue

        if page_count >= BOOK_PAGE_THRESHOLD:
            msg = (
                f"📚 <b>책 분량 PDF 발견</b> ({page_count}쪽)\n"
                f"{pdf_path.name}\n\n"
                f"Claude Code 세션에서 아래 명령으로 스킬 변환:\n"
                f"<code>/book-to-skill \"{pdf_path}\"</code>"
            )
            subprocess.run(["python3", str(SEND_MSG), msg], check=False)
            print(f"알림 발송: {pdf_path.name} ({page_count}쪽)")
        else:
            print(f"짧은 문서(스킵): {pdf_path.name} ({page_count}쪽) — /ingest로 처리 권장")

        notified.add(key)
        changed = True

    if changed:
        save_notified(notified)


if __name__ == "__main__":
    main()
