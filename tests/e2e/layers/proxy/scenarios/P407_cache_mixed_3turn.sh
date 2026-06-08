#!/bin/bash
# P407: 混合模式 s→ns→s T+R 3轮缓存
# 矩阵: TITO×交替(s→ns→s)×T+R×3轮×session
# 验证目标: 跨存储模式缓存兼容 + T+R tool_calls 正确回传
# 校验公式:
#   校验1: cache_hit_tokens[R_n] == len(full_conversation_token_ids[R_{n-1}])
#   校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P407: 混合模式 3轮缓存 ==="

RUN_ID="run-p407"
SESS_ID="sess-f307-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"
TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

log_step "注册 TITO + tool_parser + reasoning_parser"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

CITIES=("Shanghai" "Beijing" "Shenzhen")

# 从流式响应提取完整assistant消息（content+reasoning+tool_calls）
# 输出JSON: {"content":"...", "reasoning":"...", "tool_calls":[...] or None}
parse_stream_to_msg() {
    local stream_text="$1"
    echo "$stream_text" | python3 -c "
import json, sys

content_parts = []
reasoning_parts = []
tool_calls_map = {}

for line in sys.stdin:
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

# 从非流式响应提取完整assistant消息（content+reasoning+tool_calls）
# 输出JSON: {"content":"...", "reasoning":"...", "tool_calls":[...] or None}
parse_ns_to_msg() {
    local body="$1"
    echo "$body" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
msg = data['choices'][0]['message']
content = msg.get('content', '') or ''
reasoning = msg.get('reasoning_content', '') or msg.get('reasoning', '') or ''
tool_calls = msg.get('tool_calls')
# tool_calls 保持原始结构（含 id, function.name, function.arguments）
result = {
    'content': content,
    'reasoning': reasoning,
    'tool_calls': tool_calls
}
print(json.dumps(result, ensure_ascii=False))
" 2>/dev/null
}

# 构造增量 messages：从 msg_data 构建 assistant + tool role 消息
build_incremental_messages() {
    # 参数通过环境变量传入: PREV_MESSAGES, MSG_DATA, NEXT_USER_CONTENT
    python3 -c "
import json, os

prev_messages = json.loads(os.environ['PREV_MESSAGES'])
msg_data = json.loads(os.environ['MSG_DATA'])
next_content = os.environ['NEXT_USER_CONTENT']

# 构造 assistant 消息
    assistant_msg = {'role': 'assistant'}
    content = msg_data.get('content')
    if content is not None and content != '':
        assistant_msg['content'] = content
    elif not msg_data.get('tool_calls'):
        assistant_msg['content'] = ''
    reasoning = msg_data.get('reasoning_content') or msg_data.get('reasoning') or ''
    if reasoning:
        assistant_msg['reasoning'] = reasoning
        assistant_msg['reasoning_content'] = reasoning
    tool_calls = msg_data.get('tool_calls')
    if tool_calls:
        assistant_msg['tool_calls'] = tool_calls

messages = prev_messages + [assistant_msg]
# 添加 tool role 消息（如有 tool_calls）
if tool_calls:
    for tc in tool_calls:
        messages.append({'role': 'tool', 'tool_call_id': tc['id'],
                         'content': json.dumps({'temperature': 22, 'condition': 'sunny'})})
messages.append({'role': 'user', 'content': next_content})
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null
}

# ===== 第1轮: 流式 s =====
log_step "第1轮: 流式 s (${CITIES[0]})"
R1_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in ${CITIES[0]}? Think then call tool.\"}],\"tools\":${TOOLS},\"max_tokens\":256,\"stream\":true}")
assert_contains "$R1_STREAM" "data:" "第1轮流式包含 data:"
R1_MSG=$(parse_stream_to_msg "$R1_STREAM")
sleep 1

# ===== 第2轮: 非流式 ns（增量messages含R1 assistant+tool_calls+tool role） =====
log_step "第2轮: 非流式 ns (${CITIES[1]})"
R2_MESSAGES=$(PREV_MESSAGES='[{"role":"user","content":"Weather in Shanghai? Think then call tool."}]' MSG_DATA="$R1_MSG" NEXT_USER_CONTENT="Weather in Beijing? Think then call tool." build_incremental_messages)

R2_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"tools\":${TOOLS},\"max_tokens\":256}")
R2_BODY=$(echo "$R2_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R2_RESP" | sed -n '$p')" "第2轮 ns 200"
R2_MSG=$(parse_ns_to_msg "$R2_BODY")
sleep 1

# ===== 第3轮: 流式 s（增量messages含R1+R2 assistant+tool_calls+tool roles） =====
log_step "第3轮: 流式 s (${CITIES[2]})"
R3_MESSAGES=$(PREV_MESSAGES="$R2_MESSAGES" MSG_DATA="$R2_MSG" NEXT_USER_CONTENT="Weather in Shenzhen? Think then call tool." build_incremental_messages)

R3_STREAM=$(curl_with_log -s --no-buffer -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R3_MESSAGES},\"tools\":${TOOLS},\"max_tokens\":256,\"stream\":true}")
assert_contains "$R3_STREAM" "data:" "第3轮流式包含 data:"
sleep 1

# ===== 缓存校验 =====
verify_cache_incremental "$SESS_ID" 3 "incremental"

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary