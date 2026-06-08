#!/bin/bash
# P404: TITO流式T+R 3轮缓存
# 矩阵: TITO×stream×T+R×3轮×session
# 来源: TEST_CASE_CATALOG §6
# 校验公式:
#   校验1: cache_hit_tokens[R_n] == len(full_conversation_token_ids[R_{n-1}])
#   校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P404: TITO流式T+R 3轮缓存 ==="

RUN_ID="run-p404"
SESS_ID="sess-f304-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"
TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

log_step "注册TITO+tool_parser+reasoning_parser"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

CITIES=("Shanghai" "Beijing" "Shenzhen")

# 从流式响应提取完整assistant消息（content+reasoning+tool_calls）
# 输出JSON: {"content":"...", "reasoning":"...", "tool_calls":[...] or None}
parse_stream_to_msg() {
    local stream_file="$1"
    python3 -c "
import json, os

content_parts = []
reasoning_parts = []
tool_calls_map = {}

with open(os.environ['STREAM_FILE'], 'r') as f:
    for line in f:
        line = line.strip()
        if not line.startswith('data: '):
            continue
        payload = line[6:]
        if payload == '[DONE]':
            break
        try:
            data = json.loads(payload)
            for choice in data.get('choices', []):
                delta = choice.get('delta', {})
                rc = delta.get('reasoning_content', '') or delta.get('reasoning', '') or ''
                c = delta.get('content', '') or ''
                if rc: reasoning_parts.append(rc)
                if c: content_parts.append(c)
                for tc_delta in delta.get('tool_calls', []):
                    idx = tc_delta.get('index', 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            'id': '', 'type': 'function',
                            'function': {'name': '', 'arguments': ''}
                        }
                    tc_entry = tool_calls_map[idx]
                    if tc_delta.get('id'):
                        tc_entry['id'] = tc_delta['id']
                    fn = tc_delta.get('function', {})
                    if fn.get('name'):
                        tc_entry['function']['name'] = fn['name']
                    if fn.get('arguments'):
                        tc_entry['function']['arguments'] += fn['arguments']
        except json.JSONDecodeError:
            continue

result = {
    'content': ''.join(content_parts),
    'reasoning': ''.join(reasoning_parts),
    'tool_calls': [tool_calls_map[i] for i in sorted(tool_calls_map.keys())] if tool_calls_map else None
}
print(json.dumps(result, ensure_ascii=False))
" 2>/dev/null
}

# 第1轮：仅user消息+tools，流式
log_step "第1轮流式T+R请求 (${CITIES[0]})"
R1_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in ${CITIES[0]}? Think first then call tool.\"}],\"tools\":${TOOLS},\"max_tokens\":256,\"stream\":true}")
assert_contains "$R1_STREAM" "data:" "第1轮流式包含data:"
R1_TMP=$(mktemp)
echo "$R1_STREAM" > "$R1_TMP"
R1_MSG=$(STREAM_FILE="$R1_TMP" parse_stream_to_msg "$R1_TMP")
rm -f "$R1_TMP"
sleep 1

# 构造第2轮增量messages（含第1轮assistant+tool_calls回传）
log_step "构造第2轮增量messages"
R2_MESSAGES=$(R1_MSG="$R1_MSG" python3 -c "
import json, os
m1 = json.loads(os.environ['R1_MSG'])

assistant_msg = {'role': 'assistant'}
content = m1.get('content')
if content is not None and content != '':
    assistant_msg['content'] = content
elif not m1.get('tool_calls'):
    assistant_msg['content'] = ''
tool_calls = m1.get('tool_calls')
if tool_calls:
    assistant_msg['tool_calls'] = tool_calls

messages = [{'role': 'user', 'content': 'Weather in Shanghai? Think first then call tool.'}]
messages.append(assistant_msg)
if tool_calls:
    for tc in tool_calls:
        messages.append({'role': 'tool', 'tool_call_id': tc['id'],
                         'content': json.dumps({'temperature': 22, 'condition': 'sunny'})})
messages.append({'role': 'user', 'content': 'Weather in Beijing? Think first then call tool.'})
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)

# 第2轮：含历史+tools，流式
log_step "第2轮流式T+R请求 (${CITIES[1]})"
R2_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"tools\":${TOOLS},\"max_tokens\":256,\"stream\":true}")
assert_contains "$R2_STREAM" "data:" "第2轮流式包含data:"
R2_TMP=$(mktemp)
echo "$R2_STREAM" > "$R2_TMP"
R2_MSG=$(STREAM_FILE="$R2_TMP" parse_stream_to_msg "$R2_TMP")
rm -f "$R2_TMP"
sleep 1

# 构造第3轮增量messages（含第1+2轮assistant+tool_calls回传）
log_step "构造第3轮增量messages"
R3_MESSAGES=$(R1_MSG="$R1_MSG" R2_MSG="$R2_MSG" python3 -c "
import json, os

def build_assistant(msg_data):
    assistant_msg = {'role': 'assistant'}
    content = msg_data.get('content')
    if content is not None and content != '':
        assistant_msg['content'] = content
    elif not msg_data.get('tool_calls'):
        assistant_msg['content'] = ''
    tool_calls = msg_data.get('tool_calls')
    if tool_calls:
        assistant_msg['tool_calls'] = tool_calls
    return assistant_msg, tool_calls

m1 = json.loads(os.environ['R1_MSG'])
m2 = json.loads(os.environ['R2_MSG'])
a1, tc1 = build_assistant(m1)
a2, tc2 = build_assistant(m2)

messages = [{'role': 'user', 'content': 'Weather in Shanghai? Think first then call tool.'}]
messages.append(a1)
if tc1:
    for tc in tc1:
        messages.append({'role': 'tool', 'tool_call_id': tc['id'],
                         'content': json.dumps({'temperature': 22, 'condition': 'sunny'})})
messages.append({'role': 'user', 'content': 'Weather in Beijing? Think first then call tool.'})
messages.append(a2)
if tc2:
    for tc in tc2:
        messages.append({'role': 'tool', 'tool_call_id': tc['id'],
                         'content': json.dumps({'temperature': 18, 'condition': 'cloudy'})})
messages.append({'role': 'user', 'content': 'Weather in Shenzhen? Think first then call tool.'})
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)

# 第3轮：含完整历史+tools，流式
log_step "第3轮流式T+R请求 (${CITIES[2]})"
R3_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R3_MESSAGES},\"tools\":${TOOLS},\"max_tokens\":256,\"stream\":true}")
assert_contains "$R3_STREAM" "data:" "第3轮流式包含data:"

# 验证缓存递增（校验1+校验2）
verify_cache_incremental "$SESS_ID" 3 "incremental"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary