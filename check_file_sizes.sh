#!/bin/bash
# check_file_sizes.sh — 핵심 스크립트 비대화 감시
# 관제탑 파일은 500줄, 일반 파이썬 파일은 800줄을 넘으면 텔레그램 경고.
# 매주 월요일 09:00 launchd(com.hermes.sizewatch)로 자동 실행. 수동 실행도 가능.

SCRIPTS=/Users/bluesea/Applications/Mjauto/Scripts
ALERTS=""

# 관제탑 파일 (500줄 상한 — 로직은 modules/로 분리할 것)
for f in harness_agent.py hermes_local.py; do
    [ -f "$SCRIPTS/$f" ] || continue
    lines=$(wc -l < "$SCRIPTS/$f")
    if [ "$lines" -gt 500 ]; then
        ALERTS+="⚠️ 관제탑 $f: ${lines}줄 (상한 500)\n"
    fi
done

# 전체 .py (800줄 상한 — CLAUDE.md 규칙)
while IFS= read -r f; do
    lines=$(wc -l < "$f")
    if [ "$lines" -gt 800 ]; then
        ALERTS+="📏 $(basename "$f"): ${lines}줄 (상한 800)\n"
    fi
done < <(find "$SCRIPTS" "$SCRIPTS/modules" "$SCRIPTS/handlers" -maxdepth 1 -name "*.py" 2>/dev/null)

if [ -n "$ALERTS" ]; then
    MSG="🏗️ [비대화 감시] 분할 검토 필요:\n${ALERTS}"
    echo -e "$MSG"
    python3 "$SCRIPTS/send_telegram_msg.py" "$(echo -e "$MSG")"
else
    echo "✅ 모든 핵심 파일 크기 정상"
fi
