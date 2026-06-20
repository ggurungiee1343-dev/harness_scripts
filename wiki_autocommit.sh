#!/bin/bash
# Wiki 자동 커밋 — 변경사항 있을 때만 커밋

WIKI_DIR="/Users/bluesea/Applications/Mjobsidian"
LOG="/tmp/wiki_autocommit.log"
DATE=$(date +%Y-%m-%d)

cd "$WIKI_DIR" || exit 1

git add -A

if git diff --cached --quiet; then
    echo "[$DATE] 변경 없음 — 커밋 건너뜀" >> "$LOG"
else
    git commit -m "auto: wiki $DATE" >> "$LOG" 2>&1
    echo "[$DATE] 커밋 완료" >> "$LOG"
fi
