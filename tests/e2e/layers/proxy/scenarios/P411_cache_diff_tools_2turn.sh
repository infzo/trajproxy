#!/bin/bash
# P411: TITO ns Tool 2轮 tools变更缓存失效
# 矩阵: TITO×ns×Tool×2轮×session=有×tools=不同
# 来源: F3xx异常缓存矩阵 - tools变更导致缓存失效

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P411: TITO ns Tool 2轮 tools变更缓存失效 ==="

RUN_ID="run-p411"
SESS_ID="sess-f311-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

# Tool定义1：get_weather only
TOOLS1='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

# Tool定义2：get_weather + get_time（tools变更）
TOOLS2='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}},{"type":"function","function":{"name":"get_time","parameters":{"type":"object","properties":{"timezone":{"type":"string"}}}}}]'

log_step "注册 TITO + tool_parser + reasoning_parser"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

# ===== 第1轮: tools1（get_weather only） =====
log_step "第1轮 非流式 Tool 请求（tools1: get_weather）"
R1_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in Beijing?\"}],\"tools\":${TOOLS1},\"max_tokens\":128}")
R1_STATUS=$(echo "$R1_RESP" | sed -n '$p')
assert_http_status "200" "$R1_STATUS" "第1轮 tools1 200"
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

# ===== 第2轮: tools2（get_weather + get_time），tools定义不同 =====
log_step "第2轮 非流式 Tool 请求（tools2: get_weather+get_time，tools变更）"
# 第2轮包含第1轮完整历史，缓存因tools key不匹配而失效
R2_MESSAGES=$(R1_MSG="$R1_MSG" python3 -c "
import json, os
r1_msg = json.loads(os.environ['R1_MSG'])
messages = [{'role':'user','content':'Weather in Beijing?'}]
messages.append(r1_msg)
if r1_msg.get('tool_calls'):
    for tc in r1_msg['tool_calls']:
        messages.append({'role':'tool','tool_call_id':tc['id'],
                         'content':json.dumps({'temperature':18,'condition':'cloudy'})})
messages.append({'role':'user','content':'What time is it in Beijing?'})
print(json.dumps(messages, ensure_ascii=False))
")
R2_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"tools\":${TOOLS2},\"max_tokens\":128}")
R2_STATUS=$(echo "$R2_RESP" | sed -n '$p')
assert_http_status "200" "$R2_STATUS" "第2轮 tools2 200"
R2_BODY=$(echo "$R2_RESP" | sed '$d')
sleep 1

# ===== 缓存校验：tools变更导致第2轮cache_hit_tokens=0 =====
verify_cache_incremental "$SESS_ID" 2 "tools_change"

# 清理
curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary