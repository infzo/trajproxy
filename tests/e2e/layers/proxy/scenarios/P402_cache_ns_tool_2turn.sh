#!/bin/bash
# P402: TITO非流式Tool2轮缓存
# 矩阵: TITO×ns×Tool×2轮×session
# 来源: TEST_CASE_CATALOG §6
# 校验公式:
#   校验1: cache_hit_tokens[R_n] == len(full_conversation_token_ids[R_{n-1}])
#   校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P402: TITO非流式Tool2轮缓存 ==="

RUN_ID="run-p402"
SESS_ID="sess-f302-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"
TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

log_step "注册TITO+tool_parser+reasoning_parser"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

# 第1轮：仅user消息+tools
log_step "第1轮非流式Tool请求"
R1_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Shanghai?\"}],\"tools\":${TOOLS},\"max_tokens\":256}")
R1_BODY=$(echo "$R1_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R1_RESP" | sed -n '$p')" "第1轮 200"
sleep 1

# 构造第2轮增量messages（含assistant回复+tool_calls回传）
log_step "构造第2轮增量messages"
R2_MESSAGES=$(R1_BODY="$R1_BODY" python3 -c "
import json, os
data = json.loads(os.environ['R1_BODY'])
msg = data['choices'][0]['message']

# 构造assistant消息，保留原始content和tool_calls
assistant_msg = {'role': 'assistant'}
content = msg.get('content')
if content is not None:
    assistant_msg['content'] = content
elif not msg.get('tool_calls'):
    assistant_msg['content'] = ''
tool_calls = msg.get('tool_calls')
if tool_calls:
    assistant_msg['tool_calls'] = tool_calls

messages = [{'role': 'user', 'content': 'What is the weather in Shanghai?'}]
messages.append(assistant_msg)

# 如有tool_calls，追加tool role响应
if tool_calls:
    for tc in tool_calls:
        messages.append({
            'role': 'tool',
            'tool_call_id': tc['id'],
            'content': json.dumps({'temperature': 22, 'condition': 'sunny'})
        })

messages.append({'role': 'user', 'content': 'What is the weather in Beijing?'})
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)

# 第2轮：含历史+tools
log_step "第2轮非流式Tool请求（含历史）"
R2_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"tools\":${TOOLS},\"max_tokens\":256}")
R2_BODY=$(echo "$R2_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R2_RESP" | sed -n '$p')" "第2轮 200"

# 验证缓存递增（校验1+校验2）
verify_cache_incremental "$SESS_ID" 2 "incremental"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary