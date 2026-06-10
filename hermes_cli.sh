#!/bin/bash
echo "🤖 Hermes V2.5 터미널 대화형 래퍼 시작..."
echo "Launchd 서비스와 충돌하지 않도록 로그만 바로 띄우거나, 직접 CLI로 실행할 수 있습니다."
echo ""
echo "[1] 에러 로그 실시간 확인 (tail -f)"
echo "[2] Launchd 백그라운드 서비스 재시작 (launchctl)"
echo "[3] 터미널에서 직접 실행 (포그라운드 디버깅)"
echo "선택하세요 [1-3]: "
read choice

case $choice in
    1)
        tail -f /Users/bluesea/Applications/Mjauto/Scripts/hermes_launchd.error.log
        ;;
    2)
        launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hermes.bot.plist 2>/dev/null
        launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.bot.plist
        echo "✅ 재시작 완료!"
        ;;
    3)
        echo "⚠️ 주의: 포그라운드 실행 전 백그라운드 봇을 중지해야 충돌이 없습니다."
        launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hermes.bot.plist 2>/dev/null
        export PYTHONPATH=/Users/bluesea/Applications/Mjauto/Scripts:/Users/bluesea/Applications/Mjauto/Scripts/modules:$PYTHONPATH
        /usr/local/bin/python3 /Users/bluesea/Applications/Mjauto/Scripts/hermes_local.py
        ;;
    *)
        echo "❌ 잘못된 선택입니다."
        ;;
esac
