#!/bin/bash
# 场景 008: Token场景
# 测试流程：12345端口注册带run-id模型（token_in_token_out=true）-> 发送非流式推理请求 -> 查询轨迹验证response_ids -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 008: Token场景"
echo "========================================"
echo ""

# 测试配置
TOKEN_TEST_PORT=12345
TOKEN_TEST_BASE_URL="http://127.0.0.1:${TOKEN_TEST_PORT}"
TOKEN_TEST_MODEL_NAME="token-test-model"
TOKEN_TEST_RUN_ID="token-run-008"
TOKEN_TEST_SESSION_ID="${TOKEN_TEST_RUN_ID},sample008,task008"
TOKEN_TEST_TOKENIZER_PATH="Qwen/Qwen3.5-2B"

# 步骤 1: 注册模型（带 run_id，开启 token_in_token_out）
log_step "步骤 1: 注册模型（run_id: ${TOKEN_TEST_RUN_ID}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TOKEN_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TOKEN_TEST_RUN_ID}\",
        \"model_name\": \"${TOKEN_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TOKEN_TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TOKEN_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TOKEN_TEST_RUN_ID}\",
        \"model_name\": \"${TOKEN_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TOKEN_TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
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
assert_eq "$TOKEN_TEST_RUN_ID" "$REGISTER_RUN_ID" "run_id 应为 ${TOKEN_TEST_RUN_ID}"

# 验证 token_in_token_out 配置
REGISTER_TITO=$(json_get_bool "$REGISTER_BODY" "token_in_token_out")
assert_eq "true" "$REGISTER_TITO" "token_in_token_out 应为 true"

echo ""

# 步骤 2: 发送非流式推理请求
log_step "步骤 2: 发送非流式推理请求（session_id: ${TOKEN_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TOKEN_TEST_BASE_URL}/s/${TOKEN_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${TOKEN_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }'"
log_separator

CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TOKEN_TEST_BASE_URL}/s/${TOKEN_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TOKEN_TEST_MODEL_NAME}\",
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

# 步骤 3: 查询轨迹确认存在，验证 response_ids 不为空
log_step "步骤 3: 查询轨迹（session_id: ${TOKEN_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TOKEN_TEST_BASE_URL}/trajectory?session_id=${TOKEN_TEST_SESSION_ID}&limit=100'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TOKEN_TEST_BASE_URL}/trajectory?session_id=${TOKEN_TEST_SESSION_ID}&limit=100")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ_STATUS}"
log_response "${TRAJ_BODY}"
log_separator

assert_http_status "200" "$TRAJ_STATUS" "HTTP 状态码应为 200"

# 验证轨迹响应
assert_contains "$TRAJ_BODY" "session_id" "响应应包含 session_id 字段"
assert_contains "$TRAJ_BODY" "count" "响应应包含 count 字段"
assert_contains "$TRAJ_BODY" "records" "响应应包含 records 字段"

# 验证 session_id 匹配
TRAJ_SESSION_ID=$(json_get "$TRAJ_BODY" "session_id")
assert_eq "$TOKEN_TEST_SESSION_ID" "$TRAJ_SESSION_ID" "session_id 应为 ${TOKEN_TEST_SESSION_ID}"

# 验证记录数大于0
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
if [ "$TRAJ_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "轨迹记录数: ${TRAJ_COUNT}，确认轨迹已捕获"
else
    log_error "轨迹记录数应为大于0，实际为: ${TRAJ_COUNT}"
    TEST_FAILED=1
fi

# 验证 response_ids 不为空（Token模式关键字段）
if echo "$TRAJ_BODY" | grep -q '"response_ids"[[:space:]]*:[[:space:]]*\['; then
    # 检查是否为空数组
    if echo "$TRAJ_BODY" | grep -q '"response_ids"[[:space:]]*:[[:space:]]*\[\]'; then
        log_error "response_ids 为空数组，Token模式应有响应token"
        TEST_FAILED=1
    else
        log_success "response_ids 不为空，Token模式工作正常"
    fi
else
    log_error "轨迹记录中未找到 response_ids 字段"
    TEST_FAILED=1
fi

echo ""

# 步骤 4: 删除模型
log_step "步骤 4: 删除模型（run_id: ${TOKEN_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${TOKEN_TEST_BASE_URL}/models?model_name=${TOKEN_TEST_MODEL_NAME}&run_id=${TOKEN_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${TOKEN_TEST_BASE_URL}/models?model_name=${TOKEN_TEST_MODEL_NAME}&run_id=${TOKEN_TEST_RUN_ID}")

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
