#!/bin/bash
# 场景 F113: 多轮Tool调用场景（Nginx 层，非流式，两轮）
# 测试流程：注册模型（带tool_parser）-> 两轮对话（每轮均有工具调用，tools定义相同）-> 验证前缀缓存命中 -> 删除模型
# 关键验证：所有轮次使用相同的tools定义，确保system prompt一致，前缀缓存可以匹配

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F113: 多轮Tool调用场景（Nginx 层，非流式，两轮）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TOOL_TEST_BASE_URL="${BASE_URL}"
TOOL_TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
TOOL_TEST_RUN_ID="run-${SCENARIO_ID}"
TOOL_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
TOOL_TEST_TOKENIZER_PATH="${DEFAULT_TOKENIZER_PATH}"
TOOL_TEST_TOOL_PARSER="${DEFAULT_TOOL_PARSER}"
TOOL_TEST_MAX_TOKENS="${E2E_MAX_TOKENS:-2048}"

# ========== 步骤 1: 注册模型 ==========

log_step "步骤 1: 注册模型（run_id: ${TOOL_TEST_RUN_ID}, tool_parser: ${TOOL_TEST_TOOL_PARSER}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TOOL_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TOOL_TEST_RUN_ID}\",
        \"model_name\": \"${TOOL_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TOOL_TEST_TOKENIZER_PATH}\",
        \"tool_parser\": \"${TOOL_TEST_TOOL_PARSER}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TOOL_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TOOL_TEST_RUN_ID}\",
        \"model_name\": \"${TOOL_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TOOL_TEST_TOKENIZER_PATH}\",
        \"tool_parser\": \"${TOOL_TEST_TOOL_PARSER}\",
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

REGISTER_TOOL_PARSER=$(json_get "$REGISTER_BODY" "tool_parser")
assert_eq "$TOOL_TEST_TOOL_PARSER" "$REGISTER_TOOL_PARSER" "tool_parser 应为 ${TOOL_TEST_TOOL_PARSER}"

REGISTER_TITO=$(json_get_bool "$REGISTER_BODY" "token_in_token_out")
assert_eq "true" "$REGISTER_TITO" "token_in_token_out 应为 true"

sleep 2  # 等待模型注册生效

echo ""

# 定义完整的 tools 列表（两轮共用，确保前缀缓存可匹配）
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

# ========== 步骤 2: 第1轮对话（工具调用：get_weather） ==========

log_step "步骤 2: 第1轮对话（工具调用：get_weather）"
ROUND1_MESSAGES='[{"role": "user", "content": "Beijing天气怎么样？"}]'
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TOOL_TEST_BASE_URL}/s/${TOOL_TEST_RUN_ID}/${TOOL_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${TOOL_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND1_MESSAGES},
        \"tools\": ${FULL_TOOLS},
        \"stream\": false,
        \"max_tokens\": ${TOOL_TEST_MAX_TOKENS},
        ${E2E_SAMPLING_PARAMS}
    }'"
log_separator

ROUND1_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TOOL_TEST_BASE_URL}/s/${TOOL_TEST_RUN_ID}/${TOOL_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TOOL_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND1_MESSAGES},
        \"tools\": ${FULL_TOOLS},
        \"stream\": false,
        \"max_tokens\": ${TOOL_TEST_MAX_TOKENS},
        ${E2E_SAMPLING_PARAMS}
    }")

ROUND1_BODY=$(echo "$ROUND1_RESPONSE" | sed '$d')
ROUND1_STATUS=$(echo "$ROUND1_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${ROUND1_STATUS}"
log_response "${ROUND1_BODY}"
log_separator

assert_http_status "200" "$ROUND1_STATUS" "第1轮对话 HTTP 状态码应为 200"
assert_contains "$ROUND1_BODY" "choices" "第1轮响应应包含 choices 字段"

# 验证第1轮 tool_calls 字段非空
ROUND1_TOOL_DETECTED=false
if echo "$ROUND1_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*null'; then
    log_error "第1轮 tool_calls 字段为 null，应有工具调用"
    TEST_FAILED=1
elif echo "$ROUND1_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\[\]'; then
    log_error "第1轮 tool_calls 字段为空数组，应有工具调用"
    TEST_FAILED=1
elif echo "$ROUND1_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\['; then
    if echo "$ROUND1_BODY" | grep -q '"function"'; then
        log_success "第1轮 tool_calls 字段非空，检测到工具调用：get_weather"
        ROUND1_TOOL_DETECTED=true
    else
        log_error "第1轮 tool_calls 数组存在但未检测到 function 字段"
        TEST_FAILED=1
    fi
elif ! echo "$ROUND1_BODY" | grep -q '"tool_calls"'; then
    log_error "第1轮响应中无 tool_calls 字段，应有工具调用"
    TEST_FAILED=1
else
    log_error "第1轮 tool_calls 字段格式异常"
    TEST_FAILED=1
fi

# 提取第1轮 assistant 响应内容（用于后续多轮对话）
ROUND1_ASSISTANT_CONTENT=$(echo "$ROUND1_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
msg = data['choices'][0]['message']
content = msg.get('content') or ''
reasoning = msg.get('reasoning_content') or ''

# 如果没有单独的 reasoning_content，从 content 中提取 <think> 块
if not reasoning and '</think>' in content:
    parts = content.split('</think>', 1)
    reasoning = parts[0].replace('<think>', '').strip()
    content = parts[1].lstrip('\n') if len(parts) > 1 else ''

msg_dict = {'role': 'assistant'}
if msg.get('tool_calls'):
    msg_dict['tool_calls'] = msg['tool_calls']
if reasoning:
    msg_dict['reasoning_content'] = reasoning
if content:
    msg_dict['content'] = content
print(json.dumps(msg_dict))
" 2>/dev/null)

if [ -n "$ROUND1_ASSISTANT_CONTENT" ]; then
    log_success "提取第1轮 assistant 响应成功"
else
    log_error "提取第1轮 assistant 响应失败"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 3: 第2轮对话（工具调用：get_time） ==========

log_step "步骤 3: 第2轮对话（工具调用：get_time）"

# 构造第2轮 messages（使用环境变量传递数据避免 shell 引号问题）
ROUND2_MESSAGES=$(ROUND1_CONTENT="$ROUND1_ASSISTANT_CONTENT" python3 -c "
import json, os
round1_str = os.environ['ROUND1_CONTENT']
round1_assistant = json.loads(round1_str)

messages = [
    {'role': 'user', 'content': 'Beijing天气怎么样？'},
    round1_assistant,
    {'role': 'tool', 'tool_call_id': round1_assistant.get('tool_calls', [{}])[0].get('id', 'call_001'), 'content': 'Beijing weather: sunny, 25°C'},
    {'role': 'user', 'content': '上海现在几点了？'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)

if [ -z "$ROUND2_MESSAGES" ]; then
    log_error "构造第2轮 messages 失败"
    TEST_FAILED=1
fi

log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TOOL_TEST_BASE_URL}/s/${TOOL_TEST_RUN_ID}/${TOOL_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${TOOL_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND2_MESSAGES},
        \"tools\": ${FULL_TOOLS},
        \"stream\": false,
        \"max_tokens\": ${TOOL_TEST_MAX_TOKENS},
        ${E2E_SAMPLING_PARAMS}
    }'"
log_separator

# shellcheck disable=SC2086
ROUND2_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TOOL_TEST_BASE_URL}/s/${TOOL_TEST_RUN_ID}/${TOOL_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TOOL_TEST_MODEL_NAME}\",
        \"messages\": ${ROUND2_MESSAGES},
        \"tools\": ${FULL_TOOLS},
        \"stream\": false,
        \"max_tokens\": ${TOOL_TEST_MAX_TOKENS},
        ${E2E_SAMPLING_PARAMS}
    }")

ROUND2_BODY=$(echo "$ROUND2_RESPONSE" | sed '$d')
ROUND2_STATUS=$(echo "$ROUND2_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${ROUND2_STATUS}"
log_response "${ROUND2_BODY}"
log_separator

assert_http_status "200" "$ROUND2_STATUS" "第2轮对话 HTTP 状态码应为 200"
assert_contains "$ROUND2_BODY" "choices" "第2轮响应应包含 choices 字段"

# 验证第2轮 tool_calls 字段非空
ROUND2_TOOL_DETECTED=false
if echo "$ROUND2_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*null'; then
    log_error "第2轮 tool_calls 字段为 null，应有工具调用"
    TEST_FAILED=1
elif echo "$ROUND2_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\[\]'; then
    log_error "第2轮 tool_calls 字段为空数组，应有工具调用"
    TEST_FAILED=1
elif echo "$ROUND2_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\['; then
    if echo "$ROUND2_BODY" | grep -q '"function"'; then
        log_success "第2轮 tool_calls 字段非空，检测到工具调用：get_time"
        ROUND2_TOOL_DETECTED=true
    else
        log_error "第2轮 tool_calls 数组存在但未检测到 function 字段"
        TEST_FAILED=1
    fi
elif ! echo "$ROUND2_BODY" | grep -q '"tool_calls"'; then
    log_error "第2轮响应中无 tool_calls 字段，应有工具调用"
    TEST_FAILED=1
else
    log_error "第2轮 tool_calls 字段格式异常"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 4: 查询轨迹验证 ==========

log_step "步骤 4: 查询轨迹验证（session_id: ${TOOL_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TOOL_TEST_BASE_URL}/trajectory?session_id=${TOOL_TEST_SESSION_ID}&limit=100'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TOOL_TEST_BASE_URL}/trajectory?session_id=${TOOL_TEST_SESSION_ID}&limit=100")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ_STATUS}"
log_response "${TRAJ_BODY}"
log_separator

assert_http_status "200" "$TRAJ_STATUS" "查询轨迹 HTTP 状态码应为 200"

# 验证轨迹记录数为2（两轮）
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
assert_eq "2" "$TRAJ_COUNT" "轨迹记录数应为 2"

# 验证前缀缓存命中情况
CACHE_INFO=$(echo "$TRAJ_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) >= 2:
    # 按 start_time 升序排列（最早的在前）
    sorted_records = sorted(records, key=lambda r: r.get('start_time', ''))
    r1 = sorted_records[0]  # 第1轮（最早）
    r2 = sorted_records[1]  # 第2轮
    print(f\"{r1.get('cache_hit_tokens', -1)} {r2.get('cache_hit_tokens', -1)}\")
else:
    print('-1 -1')
" 2>/dev/null)

ROUND1_CACHE=$(echo "$CACHE_INFO" | awk '{print $1}')
ROUND2_CACHE=$(echo "$CACHE_INFO" | awk '{print $2}')

log_info "前缀缓存命中情况："
log_info "  第1轮 cache_hit_tokens: ${ROUND1_CACHE}"
log_info "  第2轮 cache_hit_tokens: ${ROUND2_CACHE}"

# 验证第1轮：cache_hit_tokens == 0（无历史对话）
assert_eq "0" "$ROUND1_CACHE" "第1轮 cache_hit_tokens 应为 0（无历史）"

# 验证第2轮：cache_hit_tokens > 0（复用第1轮 system prompt + tools 定义的 token 化结果）
if [ "$ROUND2_CACHE" -gt 0 ] 2>/dev/null; then
    log_success "第2轮 cache_hit_tokens=${ROUND2_CACHE} > 0，已复用第1轮缓存"
else
    log_error "第2轮 cache_hit_tokens 应 > 0（复用第1轮 system prompt + tools），实际为: ${ROUND2_CACHE}"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 5: 删除模型 ==========

log_step "步骤 5: 删除模型（run_id: ${TOOL_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${TOOL_TEST_BASE_URL}/models?model_name=${TOOL_TEST_MODEL_NAME}&run_id=${TOOL_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${TOOL_TEST_BASE_URL}/models?model_name=${TOOL_TEST_MODEL_NAME}&run_id=${TOOL_TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

DELETE_DELETED=$(json_get_bool "$DELETE_BODY" "deleted")
assert_eq "true" "$DELETE_DELETED" "deleted 应为 true"

echo ""

# ========== 验证总结 ==========
if [ "$ROUND1_TOOL_DETECTED" = true ] && [ "$ROUND2_TOOL_DETECTED" = true ]; then
    log_success "多轮Tool场景验证通过：两轮对话均检测到工具调用"
fi

# 打印测试摘要
print_summary

# 检查手动设置的失败标志（TEST_FAILED 不影响 assert_* 系列函数）
if [ "${TEST_FAILED:-0}" = "1" ]; then
    exit 1
fi
