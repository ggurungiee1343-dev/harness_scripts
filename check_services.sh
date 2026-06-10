#!/bin/bash
# ==============================================================
# Hermes 하네스 서비스 상태 점검 (v2.0 — 2026-06-07)
# 사용법: bash check_services.sh
#
# 점검 대상:
#   1. com.hermes.bot           — Hermes1 텔레그램 봇
#   2. ai.hermes2.bot           — Hermes2 텔레그램 봇
#   3. com.bluesea.hermes-webui — WebUI
#   4. ai.hermes.gateway        — Gateway
#   5. com.bluesea.fswatch-indexer — 파일 변경 감지 인덱서
#   6. llama-server port 8080   — Qwen2.5-14B-Instruct 로컬 LLM
#   7. DeepSeek / NVIDIA API    — 클라우드 API 응답 여부
#   8. 주요 파일 + WebUI 캐시 상태
# ==============================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC}   $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }

echo "=============================================="
echo " Hermes 하네스 서비스 상태 점검"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ----------------------------------------------------------
# 1. launchd 서비스
# ----------------------------------------------------------
echo ""
echo "▶ LaunchAgent 상태"

check_launchd() {
  local label="$1"
  local name="$2"
  local info
  info=$(launchctl list "$label" 2>/dev/null)
  if [ $? -eq 0 ]; then
    local pid
    pid=$(echo "$info" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('PID','-'))" 2>/dev/null)
    ok "$name  ($label)  PID=${pid:--}"
  else
    fail "$name  ($label)  미실행"
  fi
}

check_launchd "com.hermes.bot"              "Hermes1 봇"
check_launchd "ai.hermes2.bot"              "Hermes2 봇"
check_launchd "com.bluesea.hermes-webui"    "WebUI"
check_launchd "ai.hermes.gateway"           "Gateway"
check_launchd "com.bluesea.fswatch-indexer" "fswatch 인덱서"

# ----------------------------------------------------------
# 2. llama-server (로컬 LLM) — port 8080
# ----------------------------------------------------------
echo ""
echo "▶ 로컬 LLM (llama-server port 8080)"

LLAMA_RESP=$(curl -s --max-time 5 http://127.0.0.1:8080/v1/models 2>/dev/null)
if [ -n "$LLAMA_RESP" ]; then
  MODEL_ID=$(echo "$LLAMA_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ids = [m['id'] for m in d.get('data', [])]
print(ids[0] if ids else 'unknown')
" 2>/dev/null)
  ok "llama-server 응답 — 모델: ${MODEL_ID:-unknown}"
else
  fail "llama-server 응답 없음 (port 8080 timeout)"
  echo "         재기동: launchctl kickstart gui/$(id -u)/com.bluesea.llama_server2"
fi

# ----------------------------------------------------------
# 3. 현재 활성 LLM 모드
# ----------------------------------------------------------
echo ""
echo "▶ 현재 LLM 모드"

MODE_FILE="$HOME/.hermes/llm_mode.txt"
if [ -f "$MODE_FILE" ]; then
  ok "활성 모드: $(cat "$MODE_FILE")"
else
  warn "llm_mode.txt 없음 — 기본값 사용"
fi

# ----------------------------------------------------------
# 4. 외부 API 연결 (모델 목록 조회, 토큰 소모 없음)
# ----------------------------------------------------------
echo ""
echo "▶ 외부 API 연결"

SCRIPTS="$HOME/Applications/Mjauto/Scripts"
DEEPSEEK_KEY=$(python3 -c "import sys; sys.path.insert(0,'$SCRIPTS'); import config; print(config.DEEPSEEK_API_KEY or '')" 2>/dev/null)
NVIDIA_KEY=$(python3 -c "import sys; sys.path.insert(0,'$SCRIPTS'); import config; print(config.CAPT_NVIDIA_API_KEY or config.NVIDIA_API_KEY or '')" 2>/dev/null)

if [ -n "$DEEPSEEK_KEY" ]; then
  DS_STATUS=$(curl -s --max-time 8 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $DEEPSEEK_KEY" \
    "https://api.deepseek.com/v1/models" 2>/dev/null)
  [ "$DS_STATUS" = "200" ] && ok "DeepSeek API (HTTP $DS_STATUS)" || fail "DeepSeek API (HTTP $DS_STATUS)"
else
  warn "DeepSeek API 키 없음"
fi

if [ -n "$NVIDIA_KEY" ]; then
  NV_STATUS=$(curl -s --max-time 8 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $NVIDIA_KEY" \
    "https://integrate.api.nvidia.com/v1/models" 2>/dev/null)
  if [ "$NV_STATUS" = "200" ]; then
    ok "NVIDIA API (HTTP $NV_STATUS)"
  else
    fail "NVIDIA API (HTTP $NV_STATUS)"
    echo "         모델 종료 여부 확인: bash model_endpoint_check.sh"
  fi
else
  warn "NVIDIA API 키 없음"
fi

# ----------------------------------------------------------
# 5. 주요 파일 존재
# ----------------------------------------------------------
echo ""
echo "▶ 주요 파일"

check_file() {
  [ -f "$1" ] && ok "$2" || fail "$2  (없음: $1)"
}

check_file "$HOME/.hermes/config.yaml"                                                    "~/.hermes/config.yaml"
check_file "$HOME/Applications/venu/.hermes2/config.yaml"                                 "~/.hermes2/config.yaml"
check_file "$HOME/Applications/Mjauto/Scripts/harness_agent.py"                           "harness_agent.py"
check_file "$HOME/Applications/Mjauto/Scripts/hermes_local.py"                            "hermes_local.py"
check_file "$HOME/Applications/Mjauto/Scripts/hermes/memory_engine/consolidator_state.json" "consolidator_state.json"

# WebUI 캐시 신선도
CACHE="$HOME/.hermes/webui/models_cache.json"
if [ -f "$CACHE" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) ))
  if [ "$AGE" -gt 3600 ]; then
    warn "WebUI 모델 캐시 오래됨 (${AGE}초 전) — 갱신: bash clear_webui_cache.sh"
  else
    ok "WebUI 모델 캐시 신선 (${AGE}초 전 갱신)"
  fi
fi

echo ""
echo "=============================================="
echo " 완료 | 재기동: launchctl kickstart gui/$(id -u)/<label>"
echo "      | API 점검: bash model_endpoint_check.sh"
echo "      | 캐시 삭제: bash clear_webui_cache.sh"
echo "      | venv 패치 확인: bash check_venv_patches.sh"
echo "=============================================="
