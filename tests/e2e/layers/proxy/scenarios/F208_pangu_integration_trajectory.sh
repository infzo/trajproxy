#!/bin/bash
# 场景 F208: PANGU集成+轨迹抓取场景（Proxy 层）
# 测试流程：
#   1. 注册带run_id的模型
#   2. 通过 /s/{session_id}/ 路径传入UUID格式的session_id，发送非流式请求
#   3. 通过 x-sandbox-traj-id Header 传入同一个session_id，发送非流式请求
#   4. 通过 /trajectory 查询轨迹，验证两条记录都能正确抓取
#   5. 删除模型
# 特点：session_id为UUID随机字符串（非逗号分隔），两种方式传入同一个session_id

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F208: PANGU集成+轨迹抓取场景（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
PANGU_TEST_REGISTER_URL="${BASE_URL}"
PANGU_TEST_CHAT_URL="${BASE_URL}"
PANGU_TEST_MODEL_NAME="pangu-model-017"
PANGU_TEST_RUN_ID="pangu-run-017"
PANGU_TEST_MODEL_PARAM="${PANGU_TEST_MODEL_NAME},${PANGU_TEST_RUN_ID}"

# 生成UUID格式的session_id（小写）
TRAJ_SESSION_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
log_info "本次测试 session_id: ${TRAJ_SESSION_ID}"
echo ""

# ========================================
# 步骤 1: 注册模型（带 run_id）
# ========================================
log_step "步骤 1: 在${PANGU_TEST_PORT}端口注册模型（run_id: ${PANGU_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${PANGU_TEST_REGISTER_URL}/models/register' \
    -H 'Content-Type: application/json' \
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

assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 1

echo ""

# ========================================
# 步骤 2: 通过路径方式传入session_id发送请求
# ========================================
log_step "步骤 2: 通过路径 /s/{session_id}/ 传入session_id，发送非流式请求"
log_info "session_id: ${TRAJ_SESSION_ID}"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${PANGU_TEST_CHAT_URL}/s/${PANGU_TEST_RUN_ID}/${TRAJ_SESSION_ID}/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -d '{
        \"model\": \"${PANGU_TEST_MODEL_PARAM}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello from path\"}],
        \"stream\": false
    }'"
log_separator

CHAT_RESPONSE_A=$(curl -s -w "\n%{http_code}" -X POST "${PANGU_TEST_CHAT_URL}/s/${PANGU_TEST_RUN_ID}/${TRAJ_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${PANGU_TEST_MODEL_PARAM}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello from path\"}],
        \"stream\": false
    }")

CHAT_BODY_A=$(echo "$CHAT_RESPONSE_A" | sed '$d')
CHAT_STATUS_A=$(echo "$CHAT_RESPONSE_A" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS_A}"
log_response "${CHAT_BODY_A}"
log_separator

assert_http_status "200" "$CHAT_STATUS_A" "路径方式请求 HTTP 状态码应为 200"
assert_contains "$CHAT_BODY_A" "id" "路径方式响应应包含 id 字段"
assert_contains "$CHAT_BODY_A" "choices" "路径方式响应应包含 choices 字段"

echo ""

# ========================================
# 步骤 3: 通过Header方式传入session_id发送请求
# ========================================
log_step "步骤 3: 通过 x-sandbox-traj-id Header 传入session_id，发送非流式请求"
log_info "session_id: ${TRAJ_SESSION_ID}"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X POST '${PANGU_TEST_CHAT_URL}/v1/chat/completions' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \
    -H 'x-sandbox-traj-id: ${TRAJ_SESSION_ID}' \
    -d '{
        \"model\": \"${PANGU_TEST_MODEL_PARAM}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello from header\"}],
        \"stream\": false
    }'"
log_separator

CHAT_RESPONSE_B=$(curl -s -w "\n%{http_code}" -X POST "${PANGU_TEST_CHAT_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -H "x-sandbox-traj-id: ${TRAJ_SESSION_ID}" \
    -d "{
        \"model\": \"${PANGU_TEST_MODEL_PARAM}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello from header\"}],
        \"stream\": false
    }")

CHAT_BODY_B=$(echo "$CHAT_RESPONSE_B" | sed '$d')
CHAT_STATUS_B=$(echo "$CHAT_RESPONSE_B" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS_B}"
log_response "${CHAT_BODY_B}"
log_separator

assert_http_status "200" "$CHAT_STATUS_B" "Header方式请求 HTTP 状态码应为 200"
assert_contains "$CHAT_BODY_B" "id" "Header方式响应应包含 id 字段"
assert_contains "$CHAT_BODY_B" "choices" "Header方式响应应包含 choices 字段"

echo ""

# ========================================
# 步骤 4: 查询轨迹，验证记录数
# ========================================
log_step "步骤 4: 查询轨迹（session_id: ${TRAJ_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X GET '${PANGU_TEST_CHAT_URL}/trajectory?session_id=${TRAJ_SESSION_ID}'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${PANGU_TEST_CHAT_URL}/trajectory?session_id=${TRAJ_SESSION_ID}")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ_STATUS}"
log_response "${TRAJ_BODY}"
log_separator

assert_http_status "200" "$TRAJ_STATUS" "轨迹查询 HTTP 状态码应为 200"

# 验证返回的 session_id 一致
TRAJ_RETURNED_SESSION_ID=$(json_get "$TRAJ_BODY" "session_id")
assert_eq "$TRAJ_SESSION_ID" "$TRAJ_RETURNED_SESSION_ID" "轨迹查询返回的 session_id 应一致"

# 验证记录数 >= 2（两条请求都应该被记录）
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
log_info "轨迹记录数: ${TRAJ_COUNT}"

if [ "$TRAJ_COUNT" -ge 2 ] 2>/dev/null; then
    log_success "轨迹记录数 >= 2（实际: ${TRAJ_COUNT}）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "轨迹记录数应 >= 2，实际: ${TRAJ_COUNT}"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 验证记录中包含正确的 model 字段
assert_contains "$TRAJ_BODY" "${PANGU_TEST_MODEL_NAME}" "轨迹记录应包含模型名称 ${PANGU_TEST_MODEL_NAME}"

echo ""

# ========================================
# 步骤 5: 删除模型
# ========================================
log_step "步骤 5: 删除模型（run_id: ${PANGU_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \
    -X DELETE '${PANGU_TEST_REGISTER_URL}/models?model_name=${PANGU_TEST_MODEL_NAME}&run_id=${PANGU_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${PANGU_TEST_REGISTER_URL}/models?model_name=${PANGU_TEST_MODEL_NAME}&run_id=${PANGU_TEST_RUN_ID}")

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

# 打印测试摘要
print_summary
