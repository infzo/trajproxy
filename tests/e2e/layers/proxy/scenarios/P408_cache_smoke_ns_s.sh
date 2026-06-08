#!/bin/bash
# P408: TITO T+R 单轮 ns+s 降级冒烟，不验缓存
# 矩阵: TITO×(ns+s)降级×T+R×单轮×冒烟

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P408: 降级冒烟 ns+s（不验缓存）==="

RUN_ID="run-p408"
SESS_ID="sess-f308-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"
TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

log_step "注册 TITO+T+R"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

# 降级冒烟，不验缓存
# 非流式
R1=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}-ns/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"tools\":${TOOLS},\"max_tokens\":128}")
assert_http_status "200" "$(echo "$R1" | sed -n '$p')" "ns 冒烟通过"

# 流式
R2=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}-s/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Hi\"}],\"tools\":${TOOLS},\"max_tokens\":128,\"stream\":true}")
assert_contains "$R2" "data:" "s 冒烟通过"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary