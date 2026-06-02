#!/bin/bash
# 场景 F115: 多轮 Reasoning + Tool 场景（Nginx 层，非流式，三轮）
# 测试流程：注册模型（带reasoning_parser + tool_parser） -> 三轮对话（每轮均有推理+工具调用） -> 验证前缀缓存命中 -> 删除模型
# 关键验证：
#   1. 三轮对话均正确分离 reasoning 和 tool_calls
#   2. 多轮对话前缀缓存一致性（cache_hit_tokens 精确值校验）
#   3. full_conversation_token_ids 完整性校验

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F115: 多轮 Reasoning + Tool 场景（Nginx 层，非流式，三轮）"
echo "========================================"
echo ""

# 测试配置
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

log_step "步骤 1: 注册模型（run_id: ${TEST_RUN_ID}, reasoning_parser: ${TEST_REASONING_PARSER}, tool_parser: ${TEST_TOOL_PARSER}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"reasoning_parser\": \"${TEST_REASONING_PARSER}\",
        \"tool_parser\": \"${TEST_TOOL_PARSER}\",
        \"token_in_token_out\": true
    }'"
log_separator

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
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

REGISTER_REASONING=$(json_get "$REGISTER_BODY" "reasoning_parser")
assert_eq "$TEST_REASONING_PARSER" "$REGISTER_REASONING" "reasoning_parser 应为 ${TEST_REASONING_PARSER}"

REGISTER_TOOL=$(json_get "$REGISTER_BODY" "tool_parser")
assert_eq "$TEST_TOOL_PARSER" "$REGISTER_TOOL" "tool_parser 应为 ${TEST_TOOL_PARSER}"

REGISTER_TITO=$(json_get_bool "$REGISTER_BODY" "token_in_token_out")
assert_eq "true" "$REGISTER_TITO" "token_in_token_out 应为 true"

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
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city name"
                    }
                },
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
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "The timezone name"
                    }
                },
                "required": ["timezone"]
            }
        }
    }
]'

# ========== 辅助函数：提取 assistant message ==========
extract_assistant() {
    local response_body="$1"
    echo "$response_body" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
msg = data['choices'][0]['message']
content = msg.get('content') or ''

msg_dict = {'role': 'assistant'}
if msg.get('tool_calls'):
    msg_dict['tool_calls'] = msg['tool_calls']
reasoning = msg.get('reasoning_content') or msg.get('reasoning')
if reasoning:
    msg_dict['reasoning_content'] = reasoning
if content and content.strip():
    msg_dict['content'] = content
print(json.dumps(msg_dict))
" 2>/dev/null
}

# ========== 辅助函数：发送非流式请求并验证 ==========
send_round_nonstream() {
    local round_num="$1"
    local messages="$2"
    local expected_tool="$3"

    log_step "步骤 $((round_num + 1)): 第${round_num}轮对话（推理 + 工具调用：${expected_tool}, 非流式）"

    log_curl_cmd "curl -s -w '\n%{http_code}' \\
        -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions' \\
        -H 'Content-Type: application/json' \\
        -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
        -d '{
            \"model\": \"${TEST_MODEL_NAME}\",
            \"messages\": ${messages},
            \"tools\": ${FULL_TOOLS},
            \"stream\": false,
            \"max_tokens\": ${TEST_MAX_TOKENS},
            ${E2E_SAMPLING_PARAMS}
        }'"
    log_separator

    # shellcheck disable=SC2086
    ROUND_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${TEST_MODEL_NAME}\",
            \"messages\": ${messages},
            \"tools\": ${FULL_TOOLS},
            \"stream\": false,
            \"max_tokens\": ${TEST_MAX_TOKENS},
            ${E2E_SAMPLING_PARAMS}
        }")

    ROUND_BODY=$(echo "$ROUND_RESPONSE" | sed '$d')
    ROUND_STATUS=$(echo "$ROUND_RESPONSE" | sed -n '$p')

    log_response "HTTP Status: ${ROUND_STATUS}"
    log_response "${ROUND_BODY}"
    log_separator

    assert_http_status "200" "$ROUND_STATUS" "第${round_num}轮对话 HTTP 状态码应为 200"
    assert_contains "$ROUND_BODY" "choices" "第${round_num}轮响应应包含 choices 字段"

    # 验证 reasoning
    if echo "$ROUND_BODY" | grep -q '"reasoning"[[:space:]]*:[[:space:]]*""'; then
        log_error "第${round_num}轮 reasoning 字段为空字符串，应有推理内容"
        TEST_FAILED=1
    elif echo "$ROUND_BODY" | grep -q '"reasoning"[[:space:]]*:[[:space:]]*"'; then
        log_success "第${round_num}轮 reasoning 字段非空，检测到推理内容"
    else
        log_error "第${round_num}轮 reasoning 字段异常，应有推理内容"
        TEST_FAILED=1
    fi

    # 验证 tool_calls
    if echo "$ROUND_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\[\]'; then
        log_error "第${round_num}轮 tool_calls 字段为空数组，应有工具调用"
        TEST_FAILED=1
    elif echo "$ROUND_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\['; then
        if echo "$ROUND_BODY" | grep -q '"function"'; then
            log_success "第${round_num}轮 tool_calls 字段非空，检测到工具调用：${expected_tool}"
            eval "ROUND${round_num}_TOOL_DETECTED=true"
        else
            log_error "第${round_num}轮 tool_calls 数组存在但未检测到 function 字段"
            TEST_FAILED=1
        fi
    else
        log_error "第${round_num}轮响应中无 tool_calls 字段，应有工具调用"
        TEST_FAILED=1
    fi

    eval "ROUND${round_num}_BODY=\"$ROUND_BODY\""
}

# ========== 第1轮 ==========
ROUND1_MESSAGES='[{"role": "user", "content": "Beijing天气怎么样？"}]'
send_round_nonstream 1 "$ROUND1_MESSAGES" "get_weather"
ROUND1_ASSISTANT=$(extract_assistant "$ROUND1_BODY")
if [ -n "$ROUND1_ASSISTANT" ]; then
    log_success "提取第1轮 assistant 响应成功"
else
    log_error "提取第1轮 assistant 响应失败"
    TEST_FAILED=1
fi
echo ""

# ========== 第2轮 ==========
ROUND2_MESSAGES=$(ROUND1_MSG="$ROUND1_ASSISTANT" python3 -c "
import json, os
r1 = json.loads(os.environ['ROUND1_MSG'])
messages = [
    {'role': 'user', 'content': 'Beijing天气怎么样？'},
    r1,
    {'role': 'tool', 'tool_call_id': r1.get('tool_calls', [{}])[0].get('id', 'call_001'), 'content': 'Beijing weather: sunny, 25°C'},
    {'role': 'user', 'content': '上海现在几点了？'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)
if [ -z "$ROUND2_MESSAGES" ]; then log_error "构造第2轮 messages 失败"; TEST_FAILED=1; fi

send_round_nonstream 2 "$ROUND2_MESSAGES" "get_time"
ROUND2_ASSISTANT=$(extract_assistant "$ROUND2_BODY")
if [ -n "$ROUND2_ASSISTANT" ]; then
    log_success "提取第2轮 assistant 响应成功"
else
    log_error "提取第2轮 assistant 响应失败"
    TEST_FAILED=1
fi
echo ""

# ========== 第3轮 ==========
ROUND3_MESSAGES=$(ROUND1_MSG="$ROUND1_ASSISTANT" ROUND2_MSG="$ROUND2_ASSISTANT" python3 -c "
import json, os
r1 = json.loads(os.environ['ROUND1_MSG'])
r2 = json.loads(os.environ['ROUND2_MSG'])
messages = [
    {'role': 'user', 'content': 'Beijing天气怎么样？'},
    r1,
    {'role': 'tool', 'tool_call_id': r1.get('tool_calls', [{}])[0].get('id', 'call_001'), 'content': 'Beijing weather: sunny, 25°C'},
    {'role': 'user', 'content': '上海现在几点了？'},
    r2,
    {'role': 'tool', 'tool_call_id': r2.get('tool_calls', [{}])[0].get('id', 'call_002'), 'content': 'Shanghai time: 14:30 CST'},
    {'role': 'user', 'content': '深圳天气呢？'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)
if [ -z "$ROUND3_MESSAGES" ]; then log_error "构造第3轮 messages 失败"; TEST_FAILED=1; fi

send_round_nonstream 3 "$ROUND3_MESSAGES" "get_weather"
echo ""

# ========== 查询轨迹，精确验证缓存一致性 ==========
log_step "步骤 5: 查询轨迹验证前缀缓存和 token 数组一致性"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=100'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=100")
TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ_STATUS}"
log_response "${TRAJ_BODY}"
log_separator

assert_http_status "200" "$TRAJ_STATUS" "查询轨迹 HTTP 状态码应为 200"
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
assert_eq "3" "$TRAJ_COUNT" "轨迹记录数应为 3"

# 提取 cache_hit_tokens + token 数组长度
CACHE_INFO=$(echo "$TRAJ_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) >= 3:
    s = sorted(records, key=lambda r: r.get('start_time', ''))
    r1, r2, r3 = s[0], s[1], s[2]

    c1, c2, c3 = r1.get('cache_hit_tokens',-1), r2.get('cache_hit_tokens',-1), r3.get('cache_hit_tokens',-1)
    t1 = len(r1.get('token_ids') or [])
    rp1 = len(r1.get('response_ids') or [])
    f1 = len(r1.get('full_conversation_token_ids') or [])
    t2 = len(r2.get('token_ids') or [])
    rp2 = len(r2.get('response_ids') or [])
    f2 = len(r2.get('full_conversation_token_ids') or [])
    t3 = len(r3.get('token_ids') or [])
    rp3 = len(r3.get('response_ids') or [])
    f3 = len(r3.get('full_conversation_token_ids') or [])

    print(f'{c1} {c2} {c3} {t1} {rp1} {f1} {t2} {rp2} {f2} {t3} {rp3} {f3}')
else:
    print('-1 -1 -1 0 0 0 0 0 0 0 0 0')
" 2>/dev/null)

R1_CACHE=$(echo "$CACHE_INFO" | awk '{print $1}')
R2_CACHE=$(echo "$CACHE_INFO" | awk '{print $2}')
R3_CACHE=$(echo "$CACHE_INFO" | awk '{print $3}')
R1_TL=$(echo "$CACHE_INFO" | awk '{print $4}')
R1_RL=$(echo "$CACHE_INFO" | awk '{print $5}')
R1_FL=$(echo "$CACHE_INFO" | awk '{print $6}')
R2_TL=$(echo "$CACHE_INFO" | awk '{print $7}')
R2_RL=$(echo "$CACHE_INFO" | awk '{print $8}')
R2_FL=$(echo "$CACHE_INFO" | awk '{print $9}')
R3_TL=$(echo "$CACHE_INFO" | awk '{print $10}')
R3_RL=$(echo "$CACHE_INFO" | awk '{print $11}')
R3_FL=$(echo "$CACHE_INFO" | awk '{print $12}')

log_info "第1轮 cache_hit=${R1_CACHE} token_ids=${R1_TL} response_ids=${R1_RL} full_conv=${R1_FL}"
log_info "第2轮 cache_hit=${R2_CACHE} token_ids=${R2_TL} response_ids=${R2_RL} full_conv=${R2_FL}"
log_info "第3轮 cache_hit=${R3_CACHE} token_ids=${R3_TL} response_ids=${R3_RL} full_conv=${R3_FL}"

# ===== 校验 1: cache_hit_tokens == 前一轮 full_conversation_token_ids 长度 =====
log_info "--- 校验 1: cache_hit_tokens == 前一轮 full_conversation_token_ids 长度 ---"
assert_eq "0" "$R1_CACHE" "第1轮 cache_hit_tokens 应为 0（无历史）"
assert_eq "$R1_FL" "$R2_CACHE" "第2轮 cache_hit(${R2_CACHE}) == 第1轮 full_conv 长度(${R1_FL})"
assert_eq "$R2_FL" "$R3_CACHE" "第3轮 cache_hit(${R3_CACHE}) == 第2轮 full_conv 长度(${R2_FL})"

# ===== 校验 2: full_conversation_token_ids 长度 == token_ids + response_ids 长度 =====
log_info "--- 校验 2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids) ---"
R1_SUM=$((R1_TL + R1_RL))
assert_eq "$R1_SUM" "$R1_FL" "第1轮 token_ids(${R1_TL})+response_ids(${R1_RL}) == full_conv(${R1_FL})"
R2_SUM=$((R2_TL + R2_RL))
assert_eq "$R2_SUM" "$R2_FL" "第2轮 token_ids(${R2_TL})+response_ids(${R2_RL}) == full_conv(${R2_FL})"
R3_SUM=$((R3_TL + R3_RL))
assert_eq "$R3_SUM" "$R3_FL" "第3轮 token_ids(${R3_TL})+response_ids(${R3_RL}) == full_conv(${R3_FL})"

echo ""

# ========== 步骤 6: 删除模型 ==========
log_step "步骤 6: 删除模型（run_id: ${TEST_RUN_ID}）"
DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")
DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')
log_response "HTTP Status: ${DELETE_STATUS}"
assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"
assert_eq "success" "$(json_get "$DELETE_BODY" 'status')" "删除模型应返回 success"

echo ""

if [ "${ROUND1_TOOL_DETECTED:-false}" = true ] && [ "${ROUND2_TOOL_DETECTED:-false}" = true ] && [ "${ROUND3_TOOL_DETECTED:-false}" = true ]; then
    log_success "多轮 Reasoning+Tool 非流式场景验证通过：三轮对话均检测到推理内容和工具调用"
fi

print_summary
if [ "${TEST_FAILED:-0}" = "1" ]; then exit 1; fi
