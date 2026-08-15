"""Hermes1 텔레그램으로 파일(문서) 전송.

send_telegram_msg.py는 텍스트만 보낸다. 이 스크립트는 파일 첨부용이다.
자격증명 우선순위는 send_telegram_msg.py와 동일하게 맞춘다
(HERMES1_BOT_TOKEN 우선, ~/.hermes/.env 기준, chat_id는 config.ALLOWED_ID).

사용:
    python3 send_telegram_file.py 파일경로 [캡션]
    python3 send_telegram_file.py 파일1 파일2 ... --caption "설명"
"""

import mimetypes
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, "/Users/bluesea/Applications/Mjauto/Scripts")
import config  # noqa: E402 — ALLOWED_ID (Mjauto/.env)

load_dotenv("/Users/bluesea/.hermes/.env")
BOT_TOKEN = os.getenv("HERMES1_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = config.ALLOWED_ID


def send_document(file_path: str, caption: str = "") -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: HERMES1_BOT_TOKEN(~/.hermes/.env) or ALLOWED_USER_ID(Mjauto/.env) not found")
        return False

    p = Path(file_path)
    if not p.exists():
        print(f"[SKIP] 파일 없음: {file_path}")
        return False

    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > 50:
        print(f"[SKIP] 50MB 초과({size_mb:.1f}MB) — 텔레그램 봇 전송 한도: {p.name}")
        return False

    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(p, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                files={"document": (p.name, f, mime)},
                timeout=60,
            )
        if resp.ok:
            print(f"[OK] {p.name} ({size_mb:.2f}MB)")
            return True
        print(f"[FAIL] {p.name} — {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[ERROR] {p.name} — {e}")
        return False


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    caption = ""
    if "--caption" in args:
        i = args.index("--caption")
        caption = args[i + 1] if len(args) > i + 1 else ""
        args = args[:i]

    # 인자가 둘이고 두 번째가 파일이 아니면 캡션으로 본다
    if len(args) == 2 and not Path(args[1]).exists() and not caption:
        args, caption = [args[0]], args[1]

    ok = sum(send_document(a, caption) for a in args)
    print(f"\n전송 {ok}/{len(args)}")
    sys.exit(0 if ok == len(args) else 1)


if __name__ == "__main__":
    main()
