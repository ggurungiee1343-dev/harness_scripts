#!/bin/bash
# ==============================================================
# WebUI 모델 캐시 삭제 스크립트 (v1.1 — 2026-06-08)
# 사용법: bash clear_webui_cache.sh
#
# 목적: ~/.hermes/webui/models_cache.json (stale cache) 삭제 후
#       Gateway를 재시작하여 최신 모델 목록을 강제 재로드.
#
# 근거: 장애보고서 06 — WebUI 멀티모델 통합 장기화 원인 중 하나.
#       "config 변경 후 변화 없으면 이 파일 먼저 삭제 → gateway restart"
# ==============================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC}   $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }

CACHE="$HOME/.hermes/webui/models_cache.json"
GATEWAY_LABEL="ai.hermes.gateway"

echo "=============================================="
echo " WebUI 모델 캐시 삭제 + Gateway 재시작"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="
echo ""

# 1. 캐시 파일 삭제
echo "▶ 캐시 삭제"
if [ -f "$CACHE" ]; then
  rm -f "$CACHE" && ok "삭제 완료: $CACHE" || fail "삭제 실패: $CACHE"
else
  warn "캐시 파일 없음 (이미 삭제된 상태): $CACHE"
fi

# 2. Gateway 재시작
echo ""
echo "▶ Gateway 재시작 ($GATEWAY_LABEL)"
launchctl stop "$GATEWAY_LABEL" 2>/dev/null
sleep 1
launchctl start "$GATEWAY_LABEL" 2>/dev/null
sleep 2

if launchctl list "$GATEWAY_LABEL" &>/dev/null; then
  ok "Gateway 재시작 완료"
else
  fail "Gateway 재시작 실패 — 수동 확인: launchctl list $GATEWAY_LABEL"
fi

echo ""
echo "=============================================="
echo " 완료 — WebUI를 새로고침하면 최신 모델 목록이 로드됩니다."
echo "=============================================="
