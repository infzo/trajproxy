#!/bin/bash
# P410: TITO ns T+R 2轮 kwargs变化缓存失效
# 矩阵: TITO×ns×T+R×2轮×session=有×kwargs=thinking=false变
# 来源: F3xx异常缓存矩阵 - kwargs变化导致缓存失效

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P410: TITO ns T+R 2轮 kwargs变化缓存失效 ==="

RUN_ID="run-p410"
SESS_ID="sess-f310-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

# Tool定义：get_weather
TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

log_step "注册 TITO + tool_parser + reasoning_parser"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

# ===== 第1轮: enable_thinking=true =====
log_step "第1轮 非流式 Tool+Reasoning 请求（enable_thinking=true）"
R1_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in Beijing?\"}],\"tools\":${TOOLS},\"max_tokens\":256,\"chat_template_kwargs\":{\"enable_thinking\":true}}")
R1_STATUS=$(echo "$R1_RESP" | sed -n '$p')
assert_http_status "200" "$R1_STATUS" "第1轮 200"
R1_BODY=$(echo "$R1_RESP" | sed '$d')
R1_MSG=$(echo "$R1_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
msg = data['choices'][0]['message']
result = {'role': 'assistant'}
content = msg.get('content', '') or ''
if content:
    result['content'] = content
elif not msg.get('tool_calls'):
    result['content'] = ''
reasoning = msg.get('reasoning_content') or msg.get('reasoning') or ''
if reasoning:
    result['reasoning'] = reasoning
    result['reasoning_content'] = reasoning
tool_calls = msg.get('tool_calls')
if tool_calls:
    result['tool_calls'] = tool_calls
print(json.dumps(result, ensure_ascii=False))
" 2>/dev/null)
sleep 1

# ===== 第2轮: enable_thinking=false，同一session但kwargs不同 =====
log_step "第2轮 非流式 Tool+Reasoning 请求（enable_thinking=false，kwargs变化）"
R2_MESSAGES=$(R1_MSG="$R1_MSG" python3 -c "
import json, os
r1_msg = json.loads(os.environ['R1_MSG'])
messages = [{'role':'user','content':'Weather in Beijing?'}]
messages.append(r1_msg)
if r1_msg.get('tool_calls'):
    for tc in r1_msg['tool_calls']:
        messages.append({'role':'tool','tool_call_id':tc['id'],
                         'content':json.dumps({'temperature':18,'condition':'cloudy'})})
messages.append({'role':'user','content':'Weather in Shanghai?'})
print(json.dumps(messages, ensure_ascii=False))
")
R2_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"tools\":${TOOLS},\"max_tokens\":256,\"chat_template_kwargs\":{\"enable_thinking\":false}}")
R2_STATUS=$(echo "$R2_RESP" | sed -n '$p')
assert_http_status "200" "$R2_STATUS" "第2轮 200"
R2_BODY=$(echo "$R2_RESP" | sed '$d')
sleep 1

# ===== 缓存校验：kwargs变化导致第2轮cache_hit_tokens=0 =====
verify_cache_incremental "$SESS_ID" 2 "kwargs_change"

# 清理
curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary