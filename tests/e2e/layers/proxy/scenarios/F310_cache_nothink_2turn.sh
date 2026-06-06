#!/bin/bash
# F310: TITO ns T+R 2轮 kwargs变化缓存失效
# 矩阵: TITO×ns×T+R×2轮×session=有×kwargs=thinking=false变
# 来源: F3xx异常缓存矩阵 - kwargs变化导致缓存失效

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== F310: TITO ns T+R 2轮 kwargs变化缓存失效 ==="

RUN_ID="run-f310"
SESS_ID="sess-f310-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

# Tool定义：get_weather
TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

log_step "注册 TITO + tool_parser + reasoning_parser"
curl -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

# ===== 第1轮: enable_thinking=true =====
log_step "第1轮 非流式 Tool+Reasoning 请求（enable_thinking=true）"
R1_RESP=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in Beijing?\"}],\"tools\":${TOOLS},\"max_tokens\":256,\"chat_template_kwargs\":{\"enable_thinking\":true}}")
R1_STATUS=$(echo "$R1_RESP" | sed -n '$p')
assert_http_status "200" "$R1_STATUS" "第1轮 200"
R1_BODY=$(echo "$R1_RESP" | sed '$d')
R1_CONTENT=$(extract_assistant_content "$R1_BODY")
sleep 1

# ===== 第2轮: enable_thinking=false，同一session但kwargs不同 =====
log_step "第2轮 非流式 Tool+Reasoning 请求（enable_thinking=false，kwargs变化）"
# 构造增量messages（含第1轮assistant回复）
R2_MESSAGES=$(R1_CONTENT="$R1_CONTENT" python3 -c "
import json, os
messages = [{'role':'user','content':'Weather in Beijing?'},
            {'role':'assistant','content':os.environ['R1_CONTENT']},
            {'role':'user','content':'Weather in Shanghai?'}]
print(json.dumps(messages, ensure_ascii=False))
")
R2_RESP=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"tools\":${TOOLS},\"max_tokens\":256,\"chat_template_kwargs\":{\"enable_thinking\":false}}")
R2_STATUS=$(echo "$R2_RESP" | sed -n '$p')
assert_http_status "200" "$R2_STATUS" "第2轮 200"
R2_BODY=$(echo "$R2_RESP" | sed '$d')
sleep 1

# ===== 缓存校验：kwargs变化导致第2轮cache_hit_tokens=0 =====
verify_cache_incremental "$SESS_ID" 2 "kwargs_change"

# 清理
curl -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary