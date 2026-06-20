#!/bin/bash
# check_bot_alive.sh — Hermes1 텔레그램 봇 행(hang) 감시
# hermes_launchd.log / .error.log 가 N분 이상 갱신되지 않으면
# (네트워크 오류 후 폴링 루프가 멈춘 채 죽지 않는 상태) 강제 재시작.
# 5분마다 launchd(com.hermes.botwatch)로 자동 실행.

SCRIPTS=/Users/bluesea/Applications/Mjauto/Scripts
LABEL="com.hermes.bot"
STALE_MIN=3   # 하트비트는 15초마다 갱신되므로 3분 정지 = 진짜 행(hang)

# 하트비트 파일 우선 체크 (hermes_local.py가 폴링 루프마다 touch)
HEARTBEAT="$HOME/.hermes/runtime/bot_heartbeat"

now=$(date +%s)

if [ -f "$HEARTBEAT" ]; then
    mtime=$(stat -f %m "$HEARTBEAT")
else
    mtime=0
fi

if [ "$mtime" -eq 0 ]; then
    exit 0
fi

age_min=$(( (now - mtime) / 60 ))
pid=$(pgrep -f "Scripts/hermes_local.py" | head -1)

if [ "$age_min" -ge "$STALE_MIN" ] && [ -n "$pid" ]; then
    echo "[봇 행 감시] 하트비트 ${age_min}분 정지 (PID=$pid). 재시작."

    # SIGTERM 먼저 — SIGKILL은 텔레그램 서버측 잔여 연결 유발로 Conflict 루프 위험
    kill -TERM "$pid" 2>/dev/null
    for i in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null

    sleep 5
    launchctl kickstart -k "gui/501/$LABEL"
else
    echo "✅ Hermes1 정상 (하트비트 ${age_min}분 전, PID=${pid:-없음})"
fi
