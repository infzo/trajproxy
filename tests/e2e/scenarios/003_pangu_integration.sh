#!/bin/bash
# 场景 003: PANGU集成场景
# 测试流程：12300端口注册带run-id模型 -> 发送非流式推理请求到12300 -> 删除模型
# 特点：model参数格式为 {model_name},{run_id}

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 003: PANGU集成场景"
echo "========================================"
echo ""

# 测试配置
PANGU_TEST_PORT=12300
PANGU_TEST_REGISTER_URL="http://127.0.0.1:${PANGU_TEST_PORT}"
PANGU_TEST_CHAT_URL="http://127.0.0.1:${PANGU_TEST_PORT}"
PANGU_TEST_MODEL_NAME="pangu-model"
PANGU_TEST_RUN_ID="pangu-run-003"
PANGU_TEST_MODEL_PARAM="${PANGU_TEST_MODEL_NAME},${PANGU_TEST_RUN_ID}"

# 步骤 1: 在12300端口注册模型（带 run_id）
log_step "步骤 1: 在12300端口注册模型（run_id: ${PANGU_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${PANGU_TEST_REGISTER_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${PANGU_TEST_RUN_ID}\",
        \"model_name\": \"${PANGU_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${PANGU_TEST_REGISTER_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${PANGU_TEST_RUN_ID}\",
        \"model_name\": \"${PANGU_TEST_MODEL_NAME}\",
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
assert_eq "$PANGU_TEST_RUN_ID" "$REGISTER_RUN_ID" "run_id 应为 ${PANGU_TEST_RUN_ID}"

echo ""

# 步骤 2: 发送非流式推理请求到12300端口（model参数格式: {model_name},{run_id}）
log_step "步骤 2: 发送非流式推理请求到12300端口（model: ${PANGU_TEST_MODEL_PARAM}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${PANGU_TEST_CHAT_URL}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${PANGU_TEST_MODEL_PARAM}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }'"
log_separator

CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${PANGU_TEST_CHAT_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${PANGU_TEST_MODEL_PARAM}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 验证响应格式
assert_contains "$CHAT_BODY" "id" "响应应包含 id 字段"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

echo ""

# 步骤 3: 在12300端口删除模型
log_step "步骤 3: 在12300端口删除模型（run_id: ${PANGU_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${PANGU_TEST_REGISTER_URL}/models?model_name=${PANGU_TEST_MODEL_NAME}&run_id=${PANGU_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${PANGU_TEST_REGISTER_URL}/models?model_name=${PANGU_TEST_MODEL_NAME}&run_id=${PANGU_TEST_RUN_ID}")

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
