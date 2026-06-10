#!/bin/bash
# ==============================================================
# venv 패치 생존 확인 스크립트 (v1.0 — 2026-06-08)
# 사용법: bash check_venv_patches.sh
#
# 목적: pip upgrade 후 venv 내 패치가 덮어써졌는지 확인.
#
# 근거: 장애보고서 06 — "venv 패키지 pip upgrade 시
#       api_server.py, run.py 패치 덮어써짐 주의"
#
# 점검 대상:
#   1. hermes-webui/api/config.py
#      → _whitelist_keywords 에 qwen / gpt-oss-120b / minimax 포함 여부
#   2. venv/.../gateway/platforms/api_server.py
#      → @custom:<slug>: prefix strip 로직 존재 여부
#   3. venv/.../gateway/run.py
#      → _resolve_custom_provider_by_model() context_length 수정 여부
# ==============================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC}   $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; FAIL_COUNT=$((FAIL_COUNT+1)); }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }

FAIL_COUNT=0

WEBUI_CONFIG="/Users/bluesea/Applications/hermes-webui/api/config.py"
VENV_BASE="/Users/bluesea/Applications/venu/venv/lib/python3.11/site-packages"
API_SERVER="$VENV_BASE/gateway/platforms/api_server.py"
RUN_PY="$VENV_BASE/gateway/run.py"

echo "=============================================="
echo " venv 패치 생존 확인"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ----------------------------------------------------------
# 1. hermes-webui/api/config.py — _whitelist_keywords 패치
# ----------------------------------------------------------
echo ""
echo "▶ hermes-webui/api/config.py — _whitelist_keywords"

if [ ! -f "$WEBUI_CONFIG" ]; then
  warn "파일 없음: $WEBUI_CONFIG"
else
  for keyword in "qwen" "gpt-oss-120b" "minimax"; do
    if grep -qi "$keyword" "$WEBUI_CONFIG"; then
      ok "_whitelist_keywords 에 '$keyword' 포함"
    else
      fail "_whitelist_keywords 에 '$keyword' 없음 — pip upgrade 후 패치 유실 가능"
    fi
  done
fi

# ----------------------------------------------------------
# 2. gateway/platforms/api_server.py — @custom: prefix strip
# ----------------------------------------------------------
echo ""
echo "▶ gateway/platforms/api_server.py — @custom: prefix strip"

if [ ! -f "$API_SERVER" ]; then
  warn "파일 없음: $API_SERVER"
else
  # 장애보고서에 기록된 패치: "@custom:qwen-14b:" prefix strip 로직
  if grep -q "@custom" "$API_SERVER"; then
    ok "@custom: 관련 처리 로직 존재"
  else
    fail "@custom: prefix 처리 로직 없음 — 커스텀 모델 라우팅 실패 가능"
    echo "         재패치 필요: api_server.py 에 @custom:<slug>: strip 로직 추가"
  fi
fi

# ----------------------------------------------------------
# 3. gateway/run.py — _resolve_custom_provider_by_model context_length
# ----------------------------------------------------------
echo ""
echo "▶ gateway/run.py — _resolve_custom_provider_by_model context_length"

if [ ! -f "$RUN_PY" ]; then
  warn "파일 없음: $RUN_PY"
else
  if grep -q "_resolve_custom_provider_by_model" "$RUN_PY"; then
    ok "_resolve_custom_provider_by_model 함수 존재"
  else
    fail "_resolve_custom_provider_by_model 없음 — 커스텀 모델 context_length 오류 가능"
  fi

  if grep -q "context_length" "$RUN_PY"; then
    ok "context_length 처리 로직 존재"
  else
    fail "context_length 처리 로직 없음 — TypeError 발생 가능"
    echo "         재패치 필요: run.py _resolve_custom_provider_by_model() context_length 반환 수정"
  fi
fi

# ----------------------------------------------------------
# 결과 요약
# ----------------------------------------------------------
echo ""
echo "=============================================="
if [ "$FAIL_COUNT" -eq 0 ]; then
  echo -e "  ${GREEN}✅ 전체 패치 정상 — pip upgrade 후에도 패치 유지됨${NC}"
else
  echo -e "  ${RED}❌ 패치 유실 ${FAIL_COUNT}건 — 수동 재패치 필요${NC}"
  echo "  참고: wiki/00_Meta/06_에이전트_오류_및_재발방지_보고서.md"
  echo "        'WebUI 멀티모델 통합 장기화' 섹션"
fi
echo "=============================================="
