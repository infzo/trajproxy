#!/bin/bash
# 场景 F116: 多轮 Reasoning + Tool 流式场景（Nginx 层，流式，三轮）
# 测试流程：注册模型（带reasoning_parser + tool_parser） -> 三轮流式对话（每轮均有推理+工具调用） -> 验证前缀缓存命中 -> 删除模型
# 关键验证：
#   1. 流式 delta 中正确分离 reasoning_content 和 tool_calls
#   2. 从 SSE chunks 重建完整 assistant message 用于下一轮
#   3. 多轮前缀缓存精确一致性校验
#   4. full_conversation_token_ids 完整性校验

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F116: 多轮 Reasoning + Tool 流式场景（Nginx 层，流式，三轮）"
echo "========================================"
echo ""

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
TEST_TOKENIZER_PATH="${DEFAULT_TOKENIZER_PATH}"
TEST_TOOL_PARSER="${DEFAULT_TOOL_PARSER}"
TEST_REASONING_PARSER="${DEFAULT_REASONING_PARSER}"
TEST_MAX_TOKENS="${E2E_MAX_TOKENS:-2048}"

# ========== 步骤 1: 注册模型 ==========
log_step "步骤 1: 注册模型（run_id: ${TEST_RUN_ID}, reasoning: ${TEST_REASONING_PARSER}, tool: ${TEST_TOOL_PARSER}, tito: true）"
REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"reasoning_parser\": \"${TEST_REASONING_PARSER}\",
        \"tool_parser\": \"${TEST_TOOL_PARSER}\",
        \"token_in_token_out\": true
    }")
REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')
log_response "HTTP Status: ${REGISTER_STATUS}"
assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"
assert_eq "success" "$(json_get "$REGISTER_BODY" 'status')" "注册模型应返回 success"
assert_eq "$TEST_REASONING_PARSER" "$(json_get "$REGISTER_BODY" 'reasoning_parser')" "reasoning_parser 正确"
assert_eq "$TEST_TOOL_PARSER" "$(json_get "$REGISTER_BODY" 'tool_parser')" "tool_parser 正确"
assert_eq "true" "$(json_get_bool "$REGISTER_BODY" 'token_in_token_out')" "tito 应为 true"
sleep 0.5
echo ""

FULL_TOOLS='[
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "The city name"}},
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current time for a timezone",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string", "description": "The timezone name"}},
                "required": ["timezone"]
            }
        }
    }
]'

# ========== 辅助函数：从流式 SSE 响应重建 assistant message ==========
rebuild_assistant_from_stream() {
    echo "$1" | python3 -c "
import sys, json
content_parts = []
reasoning_parts = []
tool_calls_map = {}
for line in sys.stdin:
    line = line.strip()
    if not line.startswith('data:'): continue
    d = line[5:].strip()
    if d == '[DONE]': break
    try: chunk = json.loads(d)
    except: continue
    choices = chunk.get('choices', [])
    if not choices: continue
    delta = choices[0].get('delta', {}) or {}
    if delta.get('content'): content_parts.append(delta['content'])
    r = delta.get('reasoning_content') or delta.get('reasoning')
    if r: reasoning_parts.append(r)
    for tc in delta.get('tool_calls') or []:
        idx = tc.get('index', 0)
        if idx not in tool_calls_map:
            tool_calls_map[idx] = {'id':'','type':'function','function':{'name':'','arguments':''}}
        if tc.get('id'): tool_calls_map[idx]['id'] = tc['id']
        if tc.get('type'): tool_calls_map[idx]['type'] = tc['type']
        if tc.get('function'):
            fn = tc['function']
            if fn.get('name'): tool_calls_map[idx]['function']['name'] += fn['name']
            if fn.get('arguments'): tool_calls_map[idx]['function']['arguments'] += fn['arguments']
msg = {'role': 'assistant'}
tcs = [tool_calls_map[k] for k in sorted(tool_calls_map.keys())]
if tcs: msg['tool_calls'] = tcs
# 注意: reasoning_content 仅用于验证，不放入重建消息，避免污染下轮 prompt
c = ''.join(content_parts)
if c and c.strip(): msg['content'] = c
print(json.dumps(msg, ensure_ascii=False))
" 2>/dev/null
}

# ========== 辅助函数：发送流式请求并验证 ==========
send_round_stream() {
    local round_num="$1"
    local messages="$2"
    local expected_tool="$3"

    log_step "步骤 $((round_num + 1)): 第${round_num}轮流式对话（推理 + 工具调用：${expected_tool}）"
    STREAM_RESP=$(curl -s --no-buffer -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${TEST_MODEL_NAME}\",
            \"messages\": ${messages},
            \"tools\": ${FULL_TOOLS},
            \"stream\": true,
            \"max_tokens\": ${TEST_MAX_TOKENS},
            ${E2E_SAMPLING_PARAMS}
        }")

    log_response "第${round_num}轮流式响应: $(echo "$STREAM_RESP" | wc -l) lines, $(echo "$STREAM_RESP" | grep -c '^data: {' || true) chunks"
    log_separator

    assert_contains "$STREAM_RESP" "data:" "第${round_num}轮流式响应应包含 data: 前缀"
    assert_contains "$STREAM_RESP" "[DONE]" "第${round_num}轮流式响应应以 [DONE] 结束"

    # 验证 reasoning
    if echo "$STREAM_RESP" | grep -q '"reasoning_content"'; then
        log_success "第${round_num}轮流式 reasoning_content 检测到"
    elif echo "$STREAM_RESP" | grep -q '"reasoning"[[:space:]]*:[[:space:]]*"'; then
        log_success "第${round_num}轮流式 reasoning 检测到"
    else
        log_error "第${round_num}轮流式未检测到 reasoning"
        TEST_FAILED=1
    fi

    # 验证 tool_calls
    if echo "$STREAM_RESP" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\['; then
        if echo "$STREAM_RESP" | grep -q '"function"'; then
            log_success "第${round_num}轮流式 tool_calls 检测到：${expected_tool}"
            eval "ROUND${round_num}_TOOL_DETECTED=true"
        else
            log_error "第${round_num}轮流式 tool_calls 无 function 字段"
            TEST_FAILED=1
        fi
    else
        log_error "第${round_num}轮流式无 tool_calls"
        TEST_FAILED=1
    fi

    printf -v "ROUND${round_num}_STREAM" '%s' "$STREAM_RESP"
}

# ========== 第1轮 ==========
ROUND1_MSG='[{"role": "user", "content": "Beijing天气怎么样？"}]'
send_round_stream 1 "$ROUND1_MSG" "get_weather"
ROUND1_ASSISTANT=$(rebuild_assistant_from_stream "$ROUND1_STREAM")
if [ -n "$ROUND1_ASSISTANT" ]; then log_success "第1轮 assistant 重建成功"; else log_error "第1轮重建失败"; TEST_FAILED=1; fi
echo ""

# ========== 第2轮 ==========
ROUND2_MSG=$(ROUND1_MSG="$ROUND1_ASSISTANT" python3 -c "
import json, os
r1 = json.loads(os.environ['ROUND1_MSG'])
msgs = [{'role':'user','content':'Beijing天气怎么样？'}, r1,
    {'role':'tool','tool_call_id':r1.get('tool_calls',[{}])[0].get('id','call_001'),'content':'Beijing weather: sunny, 25°C'},
    {'role':'user','content':'上海现在几点了？'}]
print(json.dumps(msgs, ensure_ascii=False))
" 2>/dev/null)
if [ -z "$ROUND2_MSG" ]; then log_error "构造第2轮 messages 失败"; TEST_FAILED=1; fi
send_round_stream 2 "$ROUND2_MSG" "get_time"
ROUND2_ASSISTANT=$(rebuild_assistant_from_stream "$ROUND2_STREAM")
if [ -n "$ROUND2_ASSISTANT" ]; then log_success "第2轮 assistant 重建成功"; else log_error "第2轮重建失败"; TEST_FAILED=1; fi
echo ""

# ========== 第3轮 ==========
ROUND3_MSG=$(ROUND1_MSG="$ROUND1_ASSISTANT" ROUND2_MSG="$ROUND2_ASSISTANT" python3 -c "
import json, os
r1 = json.loads(os.environ['ROUND1_MSG'])
r2 = json.loads(os.environ['ROUND2_MSG'])
msgs = [{'role':'user','content':'Beijing天气怎么样？'}, r1,
    {'role':'tool','tool_call_id':r1.get('tool_calls',[{}])[0].get('id','call_001'),'content':'Beijing weather: sunny, 25°C'},
    {'role':'user','content':'上海现在几点了？'}, r2,
    {'role':'tool','tool_call_id':r2.get('tool_calls',[{}])[0].get('id','call_002'),'content':'Shanghai time: 14:30 CST'},
    {'role':'user','content':'深圳天气呢？'}]
print(json.dumps(msgs, ensure_ascii=False))
" 2>/dev/null)
if [ -z "$ROUND3_MSG" ]; then log_error "构造第3轮 messages 失败"; TEST_FAILED=1; fi
send_round_stream 3 "$ROUND3_MSG" "get_weather"
echo ""

# ========== 查询轨迹，精确验证 ==========
log_step "步骤 5: 查询轨迹验证缓存和 token 数组一致性"
TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=100")
TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')
assert_http_status "200" "$TRAJ_STATUS" "查询轨迹 HTTP 状态码应为 200"
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
assert_eq "3" "$TRAJ_COUNT" "轨迹记录数应为 3"

CACHE_INFO=$(echo "$TRAJ_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) >= 3:
    s = sorted(records, key=lambda r: r.get('start_time', ''))
    r1, r2, r3 = s[0], s[1], s[2]
    c1, c2, c3 = r1.get('cache_hit_tokens',-1), r2.get('cache_hit_tokens',-1), r3.get('cache_hit_tokens',-1)
    t1, rp1, f1 = len(r1.get('token_ids') or []), len(r1.get('response_ids') or []), len(r1.get('full_conversation_token_ids') or [])
    t2, rp2, f2 = len(r2.get('token_ids') or []), len(r2.get('response_ids') or []), len(r2.get('full_conversation_token_ids') or [])
    t3, rp3, f3 = len(r3.get('token_ids') or []), len(r3.get('response_ids') or []), len(r3.get('full_conversation_token_ids') or [])
    print(f'{c1} {c2} {c3} {t1} {rp1} {f1} {t2} {rp2} {f2} {t3} {rp3} {f3}')
else:
    print('-1 -1 -1 0 0 0 0 0 0 0 0 0')
" 2>/dev/null)

R1_CACHE=$(echo "$CACHE_INFO" | awk '{print $1}')
R2_CACHE=$(echo "$CACHE_INFO" | awk '{print $2}')
R3_CACHE=$(echo "$CACHE_INFO" | awk '{print $3}')
R1_TL=$(echo "$CACHE_INFO" | awk '{print $4}'); R1_RL=$(echo "$CACHE_INFO" | awk '{print $5}'); R1_FL=$(echo "$CACHE_INFO" | awk '{print $6}')
R2_TL=$(echo "$CACHE_INFO" | awk '{print $7}'); R2_RL=$(echo "$CACHE_INFO" | awk '{print $8}'); R2_FL=$(echo "$CACHE_INFO" | awk '{print $9}')
R3_TL=$(echo "$CACHE_INFO" | awk '{print $10}'); R3_RL=$(echo "$CACHE_INFO" | awk '{print $11}'); R3_FL=$(echo "$CACHE_INFO" | awk '{print $12}')

log_info "第1轮 cache_hit=${R1_CACHE} token_ids=${R1_TL} response_ids=${R1_RL} full_conv=${R1_FL}"
log_info "第2轮 cache_hit=${R2_CACHE} token_ids=${R2_TL} response_ids=${R2_RL} full_conv=${R2_FL}"
log_info "第3轮 cache_hit=${R3_CACHE} token_ids=${R3_TL} response_ids=${R3_RL} full_conv=${R3_FL}"

# ===== 校验 1 =====
log_info "--- 校验 1: cache_hit_tokens == 前一轮 full_conv 长度 ---"
assert_eq "0" "$R1_CACHE" "第1轮 cache_hit_tokens 应为 0"
assert_eq "$R1_FL" "$R2_CACHE" "第2轮 cache_hit(${R2_CACHE}) == 第1轮 full_conv(${R1_FL})"
assert_eq "$R2_FL" "$R3_CACHE" "第3轮 cache_hit(${R3_CACHE}) == 第2轮 full_conv(${R2_FL})"

# ===== 校验 2 =====
log_info "--- 校验 2: len(full_conv) == len(token_ids) + len(response_ids) ---"
R1_SUM=$((R1_TL + R1_RL)); assert_eq "$R1_SUM" "$R1_FL" "第1轮 ${R1_TL}+${R1_RL}==${R1_FL}"
R2_SUM=$((R2_TL + R2_RL)); assert_eq "$R2_SUM" "$R2_FL" "第2轮 ${R2_TL}+${R2_RL}==${R2_FL}"
R3_SUM=$((R3_TL + R3_RL)); assert_eq "$R3_SUM" "$R3_FL" "第3轮 ${R3_TL}+${R3_RL}==${R3_FL}"

echo ""

# ========== 步骤 6: 删除模型 ==========
log_step "步骤 6: 删除模型"
DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")
DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
assert_http_status "200" "$(echo "$DELETE_RESPONSE" | sed -n '$p')" "删除模型 HTTP 状态码应为 200"
assert_eq "success" "$(json_get "$DELETE_BODY" 'status')" "删除模型应返回 success"

echo ""

if [ "${ROUND1_TOOL_DETECTED:-false}" = true ] && [ "${ROUND2_TOOL_DETECTED:-false}" = true ] && [ "${ROUND3_TOOL_DETECTED:-false}" = true ]; then
    log_success "多轮 Reasoning+Tool 流式场景验证通过：三轮流式对话均检测到推理和工具调用"
fi

print_summary
if [ "${TEST_FAILED:-0}" = "1" ]; then exit 1; fi
