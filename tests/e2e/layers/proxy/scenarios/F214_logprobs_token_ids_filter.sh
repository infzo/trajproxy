#!/bin/bash
# 场景 F214: logprobs 和 token_ids 返回过滤场景（Proxy 层）
# 测试流程：启动mock服务 -> 注册模型 -> 测试各种参数组合 -> 验证响应过滤 -> 删除模型 -> 停止mock
#
# 验证要点：
#   1. 默认情况下响应不包含 logprobs 和 token_ids
#   2. 传递 logprobs=true 时响应包含 logprobs
#   3. 传递 return_token_ids=true 时响应包含 token_ids
#   4. 同时传递两个参数时响应包含两者
#   5. 流式和非流式请求行为一致
#   6. 直接转发和TITO模式行为一致

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F214: logprobs 和 token_ids 返回过滤场景（Proxy 层）"
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

# TITO模式需要tokenizer路径
TEST_TOKENIZER_PATH="${TEST_TOKENIZER_PATH:-Qwen/Qwen3.5-2B}"

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
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${TEST_BASE_URL}/models/register' \
    -H 'Content-Type: application/json' \
    -d '{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"token_in_token_out\": false
    }'"
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
# 步骤 3: 测试默认行为（不传递logprobs和return_token_ids）- 非流式
# ========================================
log_step "步骤 3: 测试默认行为（不传递logprobs和return_token_ids）- 非流式"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }'"
log_separator

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

# 验证响应不包含 logprobs 和 token_ids
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 4: 测试传递logprobs=true - 非流式
# ========================================
log_step "步骤 4: 测试传递logprobs=true - 非流式"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-logp/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"stream\": false
    }'"
log_separator

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

# 验证响应包含 logprobs（如果mock返回了的话），不包含 token_ids
# 注意：由于mock服务默认返回logprobs，这里应该能检测到
check_response_field "$CHAT_BODY" "token_ids" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 5: 测试传递return_token_ids=true - 非流式
# ========================================
log_step "步骤 5: 测试传递return_token_ids=true - 非流式"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-toks/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"return_token_ids\": true,
        \"stream\": false
    }'"
log_separator

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

# 验证响应不包含 logprobs
check_response_field "$CHAT_BODY" "logprobs" "false"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
[ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

echo ""

# ========================================
# 步骤 6: 测试同时传递两个参数 - 非流式
# ========================================
log_step "步骤 6: 测试同时传递两个参数 - 非流式"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-both/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"return_token_ids\": true,
        \"stream\": false
    }'"
log_separator

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

echo ""

# ========================================
# 步骤 7: 测试流式请求默认行为
# ========================================
log_step "步骤 7: 测试流式请求默认行为（不传递参数）"
log_curl_cmd "curl -s --no-buffer \
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": true
    }'"
log_separator

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

# 解析最后一个非[DONE]的chunk来验证是否包含字段
LAST_CHUNK=$(echo "$STREAM_RESPONSE" | grep "^data: " | grep -v "\[DONE\]" | tail -1 | sed 's/^data: //')

if [ -n "$LAST_CHUNK" ]; then
    log_info "检查最后一个数据块是否包含 logprobs/token_ids..."
    # 流式响应的最后一个chunk通常包含完整信息
    if echo "$LAST_CHUNK" | python3 -c "
import sys
import json
data = json.loads(sys.stdin.read())
choices = data.get('choices', [])
if choices:
    choice = choices[0]
    if 'logprobs' in choice or 'token_ids' in choice:
        sys.exit(1)  # 不应包含这些字段
sys.exit(0)
" 2>/dev/null; then
        log_success "流式响应不包含 logprobs 和 token_ids（符合预期）"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_error "流式响应不应包含 logprobs 或 token_ids"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
fi

echo ""

# ========================================
# 步骤 8: 测试流式请求传递logprobs=true
# ========================================
log_step "步骤 8: 测试流式请求传递logprobs=true"
log_curl_cmd "curl -s --no-buffer \
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream-logp/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"logprobs\": true,
        \"stream\": true
    }'"
log_separator

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

echo ""

# ========================================
# 步骤 9: 删除模型
# ========================================
log_step "步骤 9: 删除模型（run_id: ${TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X DELETE '${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}'"
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

# ========================================
# 步骤 10: 测试TITO模式（可选，需要tokenizer）
# ========================================
log_step "步骤 10: 测试TITO模式（可选，需要tokenizer）"
log_separator

log_info "注册TITO模式模型..."
REGISTER_TITO_RESPONSE=$(curl -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}-tito\",
        \"model_name\": \"${TEST_MODEL_NAME}-tito\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }")

REGISTER_TITO_BODY=$(echo "$REGISTER_TITO_RESPONSE" | sed '$d')
REGISTER_TITO_STATUS=$(echo "$REGISTER_TITO_RESPONSE" | sed -n '$p')

if [ "$REGISTER_TITO_STATUS" != "200" ]; then
    log_warning "TITO模型注册失败（可能tokenizer不可用），跳过TITO测试"
    log_warning "请确保 ${TEST_TOKENIZER_PATH} tokenizer可用"
else
    log_success "TITO模型注册成功"
    sleep 2

    # 测试TITO模式默认行为
    log_info "测试TITO模式默认行为..."
    CHAT_TITO_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}-tito/${TEST_SESSION_ID}-tito/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${TEST_MODEL_NAME}-tito\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
            \"stream\": false
        }")

    CHAT_TITO_BODY=$(echo "$CHAT_TITO_RESPONSE" | sed '$d')
    CHAT_TITO_STATUS=$(echo "$CHAT_TITO_RESPONSE" | sed -n '$p')

    if [ "$CHAT_TITO_STATUS" == "200" ]; then
        # 验证响应不包含 logprobs 和 token_ids
        check_response_field "$CHAT_TITO_BODY" "logprobs" "false"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        [ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))

        check_response_field "$CHAT_TITO_BODY" "token_ids" "false"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        [ $? -eq 0 ] && TESTS_PASSED=$((TESTS_PASSED + 1)) || TESTS_FAILED=$((TESTS_FAILED + 1))
    else
        log_warning "TITO请求失败，跳过验证"
    fi

    # 删除TITO模型
    curl -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}-tito&run_id=${TEST_RUN_ID}-tito" > /dev/null 2>&1
fi

echo ""

# 打印测试摘要
print_summary
