#!/bin/bash
# check_bot_alive.sh — Hermes1 텔레그램 봇 감시 & 자동 복구
# 5분마다 launchd(com.hermes.botwatch)로 자동 실행.
# 케이스 1: 프로세스 자체가 없음 (죽은 경우) → 즉시 재시작
# 케이스 2: 프로세스는 있으나 하트비트 3분 이상 정지 (hang) → 강제 재시작

SCRIPTS=/Users/bluesea/Applications/Mjauto/Scripts
LABEL="com.hermes.bot"
STALE_MIN=3
HEARTBEAT="$HOME/.hermes/runtime/bot_heartbeat"
SEND_MSG="/usr/local/bin/python3 $SCRIPTS/send_telegram_msg.py"

now=$(date +%s)
pid=$(pgrep -f "Scripts/hermes_local.py" | head -1)

_restart() {
    local reason="$1"
    echo "[botwatch] $reason — 재시작 시도"
    if [ -n "$pid" ]; then
        kill -TERM "$pid" 2>/dev/null
        for i in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    fi
    sleep 3
    # disabled 드리프트 차단: kickstart 전에 항상 enable 보장
    # (서비스가 disabled로 빠지면 kickstart가 조용히 실패 → 유령 프로세스 발생)
    launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null
    # launchd 단일 관리로 통일 — nohup 폴백 제거(유령 프로세스 원천 차단)
    # bootstrap이 필요한 경우(서비스 미로드)까지 커버
    if ! launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null; then
        echo "[botwatch] kickstart 실패 — bootstrap 재시도"
        launchctl bootstrap "gui/$(id -u)" "/Users/bluesea/Library/LaunchAgents/$LABEL.plist" 2>/dev/null
        launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null
    fi
    sleep 5
    new_pid=$(pgrep -f "Scripts/hermes_local.py" | head -1)
    if [ -n "$new_pid" ]; then
        echo "[botwatch] 복구 성공 (PID $new_pid)"
        $SEND_MSG "🔄 [botwatch] Hermes1 자동 복구 완료 (PID $new_pid) — 사유: $reason" 2>/dev/null
    else
        echo "[botwatch] 복구 실패"
        $SEND_MSG "❌ [botwatch] Hermes1 복구 실패 — 수동 확인 필요" 2>/dev/null
    fi
}

# 케이스 1: 프로세스 없음 → 즉시 재시작
if [ -z "$pid" ]; then
    _restart "프로세스 없음(봇 다운)"
    exit 0
fi

# 케이스 2: 하트비트 파일로 hang 감지
if [ -f "$HEARTBEAT" ]; then
    mtime=$(stat -f %m "$HEARTBEAT")
    age_min=$(( (now - mtime) / 60 ))
    if [ "$age_min" -ge "$STALE_MIN" ]; then
        _restart "하트비트 ${age_min}분 정지(hang)"
        exit 0
    fi
    echo "✅ Hermes1 정상 (PID=$pid, 하트비트 ${age_min}분 전)"
else
    echo "✅ Hermes1 실행 중 (PID=$pid, 하트비트 파일 없음)"
fi
