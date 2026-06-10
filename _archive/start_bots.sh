#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# [백업 보관] — 2026-05-24
# 이 스크립트는 더 이상 사용되지 않습니다.
#
# Launchd 데몬 com.hermes.bot이
#   /usr/local/bin/python3 hermes_local.py
# 를 직접 foreground에서 실행합니다.
#
# 이전 동작: python3 hermes_local.py &
# 문제: 백그라운드 실행 + KeepAlive 재실행 → 409 Conflict 무한 반복
# 해결: plist ProgramArguments에서 직접 실행 (foreground)
# ──────────────────────────────────────────────────────────────────
echo "[$(date)] ⚠️  start_bots.sh는 더 이상 사용되지 않습니다."
echo "[$(date)] Launchd → com.hermes.bot.plist → /usr/local/bin/python3 hermes_local.py"
exit 0
