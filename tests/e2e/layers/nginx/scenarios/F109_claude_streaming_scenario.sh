#!/bin/bash
# 场景 F109: Claude格式流式场景（Nginx 层）
# 测试流程：注册带run-id模型 -> 发送Claude格式流式推理请求 -> 等待返回完成 -> 删除模型
# 特点：使用 /v1/messages 端点（Claude API格式）+ 流式响应

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F109: Claude格式流式场景（Nginx 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
CLAUDE_TEST_BASE_URL="${BASE_URL}"
CLAUDE_TEST_MODEL_NAME="claude-stream-test-model"
CLAUDE_TEST_RUN_ID="run-${SCENARIO_ID}"
CLAUDE_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# 步骤 1: 注册模型（带 run_id）
log_step "步骤 1: 注册模型（run_id: ${CLAUDE_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CLAUDE_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${CLAUDE_TEST_RUN_ID}\",
        \"model_name\": \"${CLAUDE_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CLAUDE_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${CLAUDE_TEST_RUN_ID}\",
        \"model_name\": \"${CLAUDE_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
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

REGISTER_RUN_ID=$(json_get "$REGISTER_BODY" "run_id")
assert_eq "$CLAUDE_TEST_RUN_ID" "$REGISTER_RUN_ID" "run_id 应为 ${CLAUDE_TEST_RUN_ID}"

sleep 1

echo ""

# 步骤 2: 发送Claude格式流式推理请求
log_step "步骤 2: 发送Claude格式流式推理请求（run_id: ${CLAUDE_TEST_RUN_ID}, session_id: ${CLAUDE_TEST_SESSION_ID}）"
log_curl_cmd "curl -s --no-buffer \\
    -X POST '${CLAUDE_TEST_BASE_URL}/s/${CLAUDE_TEST_RUN_ID}/${CLAUDE_TEST_SESSION_ID}/v1/messages' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${CLAUDE_TEST_MODEL_NAME}\",
        \"max_tokens\": 1024,
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": true
    }'"
log_separator

# 发送流式请求并收集完整响应
STREAM_RESPONSE=$(curl -s --no-buffer -X POST "${CLAUDE_TEST_BASE_URL}/s/${CLAUDE_TEST_RUN_ID}/${CLAUDE_TEST_SESSION_ID}/v1/messages" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CLAUDE_TEST_MODEL_NAME}\",
        \"max_tokens\": 1024,
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": true
    }")

log_response "流式响应内容:"
echo "$STREAM_RESPONSE"
log_separator

# 验证流式响应格式（Claude API流式响应格式）
assert_contains "$STREAM_RESPONSE" "event:" "Claude流式响应应包含 event: 前缀"
assert_contains "$STREAM_RESPONSE" "data:" "Claude流式响应应包含 data: 字段"

# 验证是否有内容块增量事件
if echo "$STREAM_RESPONSE" | grep -q "content_block_delta"; then
    log_success "检测到 content_block_delta 事件（Claude流式响应特征）"
fi

# 验证是否有消息开始事件
if echo "$STREAM_RESPONSE" | grep -q "message_start"; then
    log_success "检测到 message_start 事件"
fi

# 验证是否有消息停止事件
if echo "$STREAM_RESPONSE" | grep -q "message_stop"; then
    log_success "检测到 message_stop 事件，流式响应正常结束"
fi

# 统计事件数量
EVENT_COUNT=$(echo "$STREAM_RESPONSE" | grep -c "^event:" || true)
log_info "收到 ${EVENT_COUNT} 个事件"

if [ "$EVENT_COUNT" -gt 0 ]; then
    log_success "Claude流式响应包含多个事件"
else
    log_error "Claude流式响应未包含有效事件"
fi

echo ""

# 步骤 3: 删除模型
log_step "步骤 3: 删除模型（run_id: ${CLAUDE_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${CLAUDE_TEST_BASE_URL}/models?model_name=${CLAUDE_TEST_MODEL_NAME}&run_id=${CLAUDE_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${CLAUDE_TEST_BASE_URL}/models?model_name=${CLAUDE_TEST_MODEL_NAME}&run_id=${CLAUDE_TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

DELETE_DELETED=$(json_get_bool "$DELETE_BODY" "deleted")
assert_eq "true" "$DELETE_DELETED" "deleted 应为 true"

echo ""

# 打印测试摘要
print_summary
