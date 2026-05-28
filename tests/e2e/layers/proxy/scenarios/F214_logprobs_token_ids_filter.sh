#!/bin/bash
# 场景 F214: logprobs 和 token_ids 强制覆盖与返回过滤（Proxy 层）
# 测试流程：启动mock服务 -> 注册模型 -> 测试各种参数组合 -> 验证上游参数和客户端响应 -> 删除模型 -> 停止mock
#
# 验证要点：
#   1. 无论客户端是否传递，上游始终收到 logprobs=1 和 return_token_ids=True
#   2. 客户端传递 logprobs=true 时，上游收到 logprobs=1（覆盖，不是 true）
#   3. 客户端传递 logprobs=5 时，上游收到 logprobs=1（覆盖）
#   4. 客户端响应始终不包含 logprobs 和 token_ids
#   5. 流式和非流式行为一致

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F214: logprobs 和 token_ids 强制覆盖与返回过滤（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="logprobs-filter-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# Mock服务配置
MOCK_PORT=19992
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_PID=""
MOCK_INFER_URL="http://${MOCK_INFER_HOST}:${MOCK_PORT}/v1"

# 确保退出时停止mock服务
trap stop_mock EXIT

# ========================================
# 辅助函数：检查响应是否包含指定字段
# ========================================
check_response_field() {
    local response="$1"
    local field="$2"
    local should_contain="$3"  # "true" 或 "false"

    if echo "$response" | python3 -c "
import sys
import json
data = json.loads(sys.stdin.read())
choices = data.get('choices', [])
if not choices:
    sys.exit(1)
choice = choices[0]
if '$field' in choice:
    sys.exit(0)  # 字段存在
else:
    sys.exit(2)  # 字段不存在
" 2>/dev/null; then
        field_exists="true"
    else
        field_exists="false"
    fi

    if [ "$should_contain" == "true" ]; then
        if [ "$field_exists" == "true" ]; then
            log_success "响应包含 $field 字段（符合预期）"
            return 0
        else
            log_error "响应应包含 $field 字段但实际不包含"
            return 1
        fi
    else
        if [ "$field_exists" == "false" ]; then
            log_success "响应不包含 $field 字段（符合预期）"
            return 0
        else
            log_error "响应不应包含 $field 字段但实际包含"
            return 1
        fi
    fi
}

# 辅助函数：验证流式响应所有chunk不含 logprobs/token_ids
check_stream_no_logprobs_token_ids() {
    local stream_response="$1"

    echo "$stream_response" | grep "^data: " | grep -v "\[DONE\]" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line.startswith('data: '):
        continue
    payload = line[6:]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        continue
    for choice in data.get('choices', []):
        if 'logprobs' in choice:
            print(f'FAIL:logprobs found in chunk: {choice}')
            sys.exit(1)
        if 'token_ids' in choice:
            print(f'FAIL:token_ids found in chunk: {choice}')
            sys.exit(1)
print('OK')
" 2>/dev/null
}

# ========================================
# 步骤 0: 清理残留模型
# ========================================
log_info "清理可能残留的模型..."
curl -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1

# ========================================
# 步骤 1: 启动Mock服务
# ========================================
log_step "步骤 1: 启动Mock推理服务"
log_separator

if ! start_mock; then
    log_error "无法启动Mock服务，测试终止"
    exit 1
fi

echo ""

# ========================================
# 步骤 2: 注册模型（直接转发模式）
# ========================================
log_step "步骤 2: 注册模型（直接转发模式）"
log_separator

REGISTER_RESPONSE=$(curl -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"token_in_token_out\": false
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 2

echo ""

# ========================================
# 步骤 3: 非流式 - 不传递参数 -> 上游强制覆盖，客户端无字段
# ========================================
log_step "步骤 3: 非流式 - 不传递 logprobs/return_token_ids"
log_separator

clear_mock_records

CHAT_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 3a: 验证客户端响应不含 logprobs 和 token_ids
log_info "验证客户端响应不含 logprobs 和 token_ids..."
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 3b: 验证上游收到强制参数 logprobs=1, return_token_ids=True
log_info "验证上游收到强制参数..."
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "上游应收到 logprobs=1（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "上游应收到 return_token_ids=True（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 4: 非流式 - 传递 logprobs=true -> 上游仍为1，客户端无字段
# ========================================
log_step "步骤 4: 非流式 - 传递 logprobs=true（应被覆盖为1）"
log_separator

clear_mock_records

CHAT_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-logp/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 4a: 客户端响应不含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 4b: 上游收到 logprobs=1（不是 true），return_token_ids=True
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "上游应收到 logprobs=1（覆盖客户端的 true）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "上游应收到 return_token_ids=True（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 5: 非流式 - 传递 logprobs=5（数值型）-> 上游覆盖为1
# ========================================
log_step "步骤 5: 非流式 - 传递 logprobs=5（应被覆盖为1）"
log_separator

clear_mock_records

CHAT_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-logp5/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": 5,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 5a: 客户端响应不含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 5b: 上游收到 logprobs=1（覆盖客户端的 5）
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "上游应收到 logprobs=1（覆盖客户端的 5）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 6: 非流式 - 传递 return_token_ids=true -> 上游为True，客户端无字段
# ========================================
log_step "步骤 6: 非流式 - 传递 return_token_ids=true"
log_separator

clear_mock_records

CHAT_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-toks/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"return_token_ids\": true,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 6a: 客户端响应不含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 6b: 上游收到强制参数
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "上游应收到 logprobs=1（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "上游应收到 return_token_ids=True"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 7: 非流式 - 同时传递两个参数
# ========================================
log_step "步骤 7: 非流式 - 同时传递 logprobs=true 和 return_token_ids=true"
log_separator

clear_mock_records

CHAT_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-both/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"return_token_ids\": true,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 7a: 客户端响应不含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

# 7b: 上游收到强制参数
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "上游应收到 logprobs=1（覆盖客户端的 true）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "上游应收到 return_token_ids=True"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 8: 流式 - 不传递参数 -> 上游强制覆盖，客户端无字段
# ========================================
log_step "步骤 8: 流式 - 不传递 logprobs/return_token_ids"
log_separator

clear_mock_records

STREAM_RESPONSE=$(curl -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": true
    }")

log_info "流式响应内容（截取前5行）:"
echo "$STREAM_RESPONSE" | head -5
log_separator

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 8a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 8b: 验证上游收到强制参数
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "流式上游应收到 logprobs=1（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "流式上游应收到 return_token_ids=True（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 9: 流式 - 传递 logprobs=true -> 上游覆盖，客户端无字段
# ========================================
log_step "步骤 9: 流式 - 传递 logprobs=true（应被覆盖为1）"
log_separator

clear_mock_records

STREAM_RESPONSE=$(curl -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream-logp/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"stream\": true
    }")

log_info "流式响应内容（截取前5行）:"
echo "$STREAM_RESPONSE" | head -5
log_separator

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 9a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 9b: 验证上游收到 logprobs=1（覆盖客户端的 true）
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "流式上游应收到 logprobs=1（覆盖客户端的 true）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "流式上游应收到 return_token_ids=True（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 10: 流式 - 传递 logprobs=5 -> 上游覆盖为1
# ========================================
log_step "步骤 10: 流式 - 传递 logprobs=5（应被覆盖为1）"
log_separator

clear_mock_records

STREAM_RESPONSE=$(curl -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream-logp5/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": 5,
        \"stream\": true
    }")

log_info "流式响应内容（截取前5行）:"
echo "$STREAM_RESPONSE" | head -5
log_separator

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 10a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 10b: 验证上游收到 logprobs=1（覆盖客户端的 5）
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "流式上游应收到 logprobs=1（覆盖客户端的 5）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 11: 流式 - 传递 return_token_ids=true -> 上游强制True，客户端无字段
# ========================================
log_step "步骤 11: 流式 - 传递 return_token_ids=true"
log_separator

clear_mock_records

STREAM_RESPONSE=$(curl -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream-toks/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"return_token_ids\": true,
        \"stream\": true
    }")

log_info "流式响应内容（截取前5行）:"
echo "$STREAM_RESPONSE" | head -5
log_separator

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 11a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 11b: 验证上游收到强制参数
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "流式上游应收到 logprobs=1（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "流式上游应收到 return_token_ids=True（强制覆盖）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 12: 流式 - 同时传递两个参数
# ========================================
log_step "步骤 12: 流式 - 同时传递 logprobs=true 和 return_token_ids=true"
log_separator

clear_mock_records

STREAM_RESPONSE=$(curl -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream-both/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"return_token_ids\": true,
        \"stream\": true
    }")

log_info "流式响应内容（截取前5行）:"
echo "$STREAM_RESPONSE" | head -5
log_separator

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

# 12a: 验证所有chunk不含 logprobs/token_ids
STREAM_CHECK=$(check_stream_no_logprobs_token_ids "$STREAM_RESPONSE")
if [ "$STREAM_CHECK" == "OK" ]; then
    log_success "流式响应所有chunk不含 logprobs 和 token_ids（符合预期）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式响应包含 logprobs 或 token_ids: $STREAM_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 12b: 验证上游收到强制参数
VERIFY_RESULT=$(verify_infer_request "logprobs return_token_ids" "")
LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "1" "$LOGPROBS_VAL" "流式上游应收到 logprobs=1（覆盖客户端的 true）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

TOKEN_IDS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:return_token_ids=" | cut -d= -f2)
assert_eq "True" "$TOKEN_IDS_VAL" "流式上游应收到 return_token_ids=True（覆盖客户端的 true）"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 13: 删除模型
# ========================================
log_step "步骤 13: 删除模型（run_id: ${TEST_RUN_ID}）"
log_separator

DELETE_RESPONSE=$(curl -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

# 打印测试摘要
print_summary
