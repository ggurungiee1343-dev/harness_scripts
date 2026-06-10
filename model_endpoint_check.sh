#!/bin/bash
# ==============================================================
# 모델 엔드포인트 생존 확인 스크립트 (v1.0 — 2026-06-07)
# 사용법: bash model_endpoint_check.sh
#
# 목적: NVIDIA NIM 70B 조용한 서비스 종료 사건(장애#024) 재발 방지.
#       모델이 HTTP 404로 죽었는지 직접 확인하여 조기 감지.
#
# 점검 항목:
#   - NVIDIA GPT OSS 120B  (openai/gpt-oss-120b)
#   - DeepSeek Chat        (deepseek-chat)
#   - Qwen 로컬 llama-server (port 8080)
# ==============================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[LIVE]${NC}  $1"; }
fail() { echo -e "  ${RED}[DEAD]${NC}  $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC}  $1"; }

SCRIPTS="$HOME/Applications/Mjauto/Scripts"

echo "=============================================="
echo " 모델 엔드포인트 생존 확인"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ----------------------------------------------------------
# 1. NVIDIA GPT OSS 120B
# ----------------------------------------------------------
echo ""
echo "▶ NVIDIA — openai/gpt-oss-120b"

NVIDIA_KEY=$(python3 -c "import sys; sys.path.insert(0,'$SCRIPTS'); import config; print(config.CAPT_NVIDIA_API_KEY or config.NVIDIA_API_KEY or '')" 2>/dev/null)

if [ -z "$NVIDIA_KEY" ]; then
  warn "NVIDIA API 키 없음 — 건너뜀"
else
  RESP=$(curl -s --max-time 20 \
    -X POST "https://integrate.api.nvidia.com/v1/chat/completions" \
    -H "Authorization: Bearer $NVIDIA_KEY" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "openai/gpt-oss-120b",
      "messages": [{"role": "user", "content": "hi"}],
      "max_tokens": 1
    }' 2>/dev/null)

  HTTP_CODE=$(curl -s --max-time 20 -o /dev/null -w "%{http_code}" \
    -X POST "https://integrate.api.nvidia.com/v1/chat/completions" \
    -H "Authorization: Bearer $NVIDIA_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"openai/gpt-oss-120b","messages":[{"role":"user","content":"hi"}],"max_tokens":1}' 2>/dev/null)

  case "$HTTP_CODE" in
    200) ok "openai/gpt-oss-120b  (HTTP $HTTP_CODE)" ;;
    404) fail "openai/gpt-oss-120b  (HTTP 404 — 모델 종료됨!)"
         echo "         → harness_agent.py 에서 model= 값 교체 필요"
         echo "         → 사용 가능 모델 목록: curl -s -H 'Authorization: Bearer \$CAPT_NVIDIA_API_KEY' https://integrate.api.nvidia.com/v1/models | python3 -m json.tool | grep '\"id\"'" ;;
    401|403) fail "openai/gpt-oss-120b  (HTTP $HTTP_CODE — 인증 실패)" ;;
    429) warn "openai/gpt-oss-120b  (HTTP 429 — Rate Limit)" ;;
    *)   fail "openai/gpt-oss-120b  (HTTP $HTTP_CODE)" ;;
  esac
fi

# ----------------------------------------------------------
# 2. DeepSeek Chat
# ----------------------------------------------------------
echo ""
echo "▶ DeepSeek — deepseek-chat"

DEEPSEEK_KEY=$(python3 -c "import sys; sys.path.insert(0,'$SCRIPTS'); import config; print(config.DEEPSEEK_API_KEY or '')" 2>/dev/null)

if [ -z "$DEEPSEEK_KEY" ]; then
  warn "DeepSeek API 키 없음 — 건너뜀"
else
  DS_CODE=$(curl -s --max-time 20 -o /dev/null -w "%{http_code}" \
    -X POST "https://api.deepseek.com/v1/chat/completions" \
    -H "Authorization: Bearer $DEEPSEEK_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hi"}],"max_tokens":1}' 2>/dev/null)

  case "$DS_CODE" in
    200) ok "deepseek-chat  (HTTP $DS_CODE)" ;;
    404) fail "deepseek-chat  (HTTP 404 — 모델 종료됨!)" ;;
    401|403) fail "deepseek-chat  (HTTP $DS_CODE — 인증 실패)" ;;
    429) warn "deepseek-chat  (HTTP 429 — Rate Limit)" ;;
    *)   fail "deepseek-chat  (HTTP $DS_CODE)" ;;
  esac
fi

# ----------------------------------------------------------
# 3. Qwen 로컬 llama-server (port 8080)
# ----------------------------------------------------------
echo ""
echo "▶ 로컬 LLM — llama-server port 8080"

LLAMA_CODE=$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" \
  http://127.0.0.1:8080/v1/models 2>/dev/null)

if [ "$LLAMA_CODE" = "200" ]; then
  MODEL_ID=$(curl -s --max-time 5 http://127.0.0.1:8080/v1/models 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'] if d.get('data') else 'unknown')" 2>/dev/null)
  ok "llama-server  (HTTP $LLAMA_CODE)  모델: ${MODEL_ID:-unknown}"
else
  fail "llama-server  (HTTP $LLAMA_CODE — port 8080 응답 없음)"
  echo "         재기동: launchctl kickstart gui/$(id -u)/com.bluesea.llama_server2"
fi

echo ""
echo "=============================================="
echo " 완료"
echo " DEAD 항목 발생 시:"
echo "   NVIDIA 모델 교체 → harness_agent.py model= 값 수정"
echo "   DeepSeek 장애   → switch_model.sh got 으로 전환"
echo "   llama-server 다운 → launchctl kickstart 로 재기동"
echo "=============================================="
