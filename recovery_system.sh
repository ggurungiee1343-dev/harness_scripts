#!/bin/bash

# 🤖 하네스 시스템 메모리 상태 진단 스크립트 (v3.0 — Safe Mode)
# 사용법: bash recovery_system.sh
# 주의: sudo purge, pkill 등 위험한 명령은 제거됨
#       실제 복구는 launchd가 KeepAlive로 자동 처리

echo "------------------------------------------"
echo "🔍 시스템 메모리 상태 진단 중..."
echo "------------------------------------------"

# 1. 메모리 상태만 확인 (청소 없음 — launchd KeepAlive 자동 복구에 위임)
echo "1. 현재 메모리 상태 조회..."
vm_stat | head -10
echo ""
echo "Free Memory:"
memory_pressure | head -5

# 2. Python 내부 GC 유도 (현재 프로세스에 한정)
echo ""
echo "2. Python GC 힌트 전달..."
echo "   → launchd KeepAlive 자동 복구에 위임 (별도 조치 불필요)"

# 3. CPU/로드 상태 확인
echo ""
echo "3. CPU/로드 상태..."
uptime

echo ""
echo "------------------------------------------"
echo "✅ 진단 완료 — launchd가 자동 복구를 처리합니다."
echo "💡 recovery가 필요한 경우: launchctl kickstart gui/$(id -u)/com.hermes.bot"
echo "------------------------------------------"
