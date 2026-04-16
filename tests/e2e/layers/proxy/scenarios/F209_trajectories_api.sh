#!/bin/bash
# 场景 F209: Trajectories API 测试用例（Proxy 层）
# 测试新的轨迹查询接口：
#   - GET /trajectories?run_id={run_id}
#   - GET /trajectories/{session_id}?limit=10000
# 同时验证旧接口 /trajectory 向后兼容性

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F209: Trajectories API 测试用例（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TRAJ_API_BASE_URL="${BASE_URL}"
TRAJ_API_MODEL_NAME="traj-api-test-model"
TRAJ_API_RUN_ID="run-${SCENARIO_ID}"

# 生成多个 session_id 用于测试
TRAJ_API_SESSION_ID_1="session-${SCENARIO_ID}-1-$(date +%s%N | md5sum | head -c 8)"
TRAJ_API_SESSION_ID_2="session-${SCENARIO_ID}-2-$(date +%s%N | md5sum | head -c 8)"
TRAJ_API_SESSION_ID_3="session-${SCENARIO_ID}-3-$(date +%s%N | md5sum | head -c 8)"

# ============================================================
# 步骤 1: 注册模型（带 run_id）
# ============================================================
log_step "步骤 1: 注册模型（run_id: ${TRAJ_API_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TRAJ_API_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TRAJ_API_RUN_ID}\",
        \"model_name\": \"${TRAJ_API_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TRAJ_API_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TRAJ_API_RUN_ID}\",
        \"model_name\": \"${TRAJ_API_MODEL_NAME}\",
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

sleep 1

echo ""

# ============================================================
# 步骤 2: 发送多个推理请求（创建多个 session）
# ============================================================
log_step "步骤 2: 发送推理请求（创建 3 个不同 session）"
log_separator

# Session 1
echo ">>> Session 1: ${TRAJ_API_SESSION_ID_1}"
CHAT_RESPONSE_1=$(curl -s -w "\n%{http_code}" -X POST "${TRAJ_API_BASE_URL}/s/${TRAJ_API_RUN_ID}/${TRAJ_API_SESSION_ID_1}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TRAJ_API_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello from session 1\"}],
        \"stream\": false
    }")

CHAT_STATUS_1=$(echo "$CHAT_RESPONSE_1" | sed -n '$p')
assert_http_status "200" "$CHAT_STATUS_1" "Session 1 推理请求应返回 200"
sleep 1

# Session 2
echo ">>> Session 2: ${TRAJ_API_SESSION_ID_2}"
CHAT_RESPONSE_2=$(curl -s -w "\n%{http_code}" -X POST "${TRAJ_API_BASE_URL}/s/${TRAJ_API_RUN_ID}/${TRAJ_API_SESSION_ID_2}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TRAJ_API_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello from session 2\"}],
        \"stream\": false
    }")

CHAT_STATUS_2=$(echo "$CHAT_RESPONSE_2" | sed -n '$p')
assert_http_status "200" "$CHAT_STATUS_2" "Session 2 推理请求应返回 200"
sleep 1

# Session 3
echo ">>> Session 3: ${TRAJ_API_SESSION_ID_3}"
CHAT_RESPONSE_3=$(curl -s -w "\n%{http_code}" -X POST "${TRAJ_API_BASE_URL}/s/${TRAJ_API_RUN_ID}/${TRAJ_API_SESSION_ID_3}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TRAJ_API_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello from session 3\"}],
        \"stream\": false
    }")

CHAT_STATUS_3=$(echo "$CHAT_RESPONSE_3" | sed -n '$p')
assert_http_status "200" "$CHAT_STATUS_3" "Session 3 推理请求应返回 200"
sleep 1

log_separator
echo ""

# ============================================================
# 步骤 3: 测试 GET /trajectories?run_id={run_id}
# ============================================================
log_step "步骤 3: 查询轨迹列表（GET /trajectories）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TRAJ_API_BASE_URL}/trajectories?run_id=${TRAJ_API_RUN_ID}'"
log_separator

SESSIONS_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TRAJ_API_BASE_URL}/trajectories?run_id=${TRAJ_API_RUN_ID}")

SESSIONS_BODY=$(echo "$SESSIONS_RESPONSE" | sed '$d')
SESSIONS_STATUS=$(echo "$SESSIONS_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${SESSIONS_STATUS}"
log_response "${SESSIONS_BODY}"
log_separator

assert_http_status "200" "$SESSIONS_STATUS" "HTTP 状态码应为 200"

# 验证响应结构
assert_contains "$SESSIONS_BODY" "run_id" "响应应包含 run_id 字段"
assert_contains "$SESSIONS_BODY" "trajectories" "响应应包含 trajectories 字段"

# 验证 run_id 匹配
RESPONSE_RUN_ID=$(json_get "$SESSIONS_BODY" "run_id")
assert_eq "$TRAJ_API_RUN_ID" "$RESPONSE_RUN_ID" "run_id 应为 ${TRAJ_API_RUN_ID}"

# 验证轨迹数量（应该至少有 3 个）
SESSION_COUNT=$(echo "$SESSIONS_BODY" | grep -o '"session_id"' | wc -l | tr -d ' ')
if [ "$SESSION_COUNT" -ge 3 ] 2>/dev/null; then
    log_success "轨迹数量: ${SESSION_COUNT}，符合预期（>= 3）"
else
    log_error "轨迹数量应为 >= 3，实际为: ${SESSION_COUNT}"
    TEST_FAILED=1
fi

# 验证返回的 session_id 包含我们创建的
assert_contains "$SESSIONS_BODY" "${TRAJ_API_SESSION_ID_1}" "应包含 session_id_1"
assert_contains "$SESSIONS_BODY" "${TRAJ_API_SESSION_ID_2}" "应包含 session_id_2"
assert_contains "$SESSIONS_BODY" "${TRAJ_API_SESSION_ID_3}" "应包含 session_id_3"

echo ""

# ============================================================
# 步骤 4: 测试 GET /trajectories 缺少 run_id 参数
# ============================================================
log_step "步骤 4: 测试缺少 run_id 参数（应返回 422 错误）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TRAJ_API_BASE_URL}/trajectories'"
log_separator

NO_RUNID_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TRAJ_API_BASE_URL}/trajectories")

NO_RUNID_BODY=$(echo "$NO_RUNID_RESPONSE" | sed '$d')
NO_RUNID_STATUS=$(echo "$NO_RUNID_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${NO_RUNID_STATUS}"
log_response "${NO_RUNID_BODY}"
log_separator

# FastAPI 对于缺少必填查询参数返回 422
if [ "$NO_RUNID_STATUS" = "422" ] || [ "$NO_RUNID_STATUS" = "400" ]; then
    log_success "缺少 run_id 参数应返回 422/400，实际返回: ${NO_RUNID_STATUS}"
else
    log_error "缺少 run_id 参数应返回 422/400，实际返回: ${NO_RUNID_STATUS}"
    TEST_FAILED=1
fi

echo ""

# ============================================================
# 步骤 5: 测试 GET /trajectories/{session_id}
# ============================================================
log_step "步骤 5: 查询指定轨迹的完整数据（GET /trajectories/{session_id}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TRAJ_API_BASE_URL}/trajectories/${TRAJ_API_SESSION_ID_1}'"
log_separator

RECORDS_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TRAJ_API_BASE_URL}/trajectories/${TRAJ_API_SESSION_ID_1}")

RECORDS_BODY=$(echo "$RECORDS_RESPONSE" | sed '$d')
RECORDS_STATUS=$(echo "$RECORDS_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${RECORDS_STATUS}"
log_response "${RECORDS_BODY}"
log_separator

assert_http_status "200" "$RECORDS_STATUS" "HTTP 状态码应为 200"

# 验证响应结构
assert_contains "$RECORDS_BODY" "session_id" "响应应包含 session_id 字段"
assert_contains "$RECORDS_BODY" "records" "响应应包含 records 字段"

# 验证 session_id 匹配
RESPONSE_SESSION_ID=$(json_get "$RECORDS_BODY" "session_id")
assert_eq "$TRAJ_API_SESSION_ID_1" "$RESPONSE_SESSION_ID" "session_id 应为 ${TRAJ_API_SESSION_ID_1}"

# 验证记录数大于 0
RECORDS_COUNT=$(echo "$RECORDS_BODY" | grep -o '"unique_id"' | wc -l | tr -d ' ')
if [ "$RECORDS_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "轨迹记录数: ${RECORDS_COUNT}，确认记录已获取"
else
    log_error "轨迹记录数应大于 0，实际为: ${RECORDS_COUNT}"
    TEST_FAILED=1
fi

# 验证记录包含必要字段
assert_contains "$RECORDS_BODY" "request_id" "记录应包含 request_id 字段"
assert_contains "$RECORDS_BODY" "model" "记录应包含 model 字段"
assert_contains "$RECORDS_BODY" "prompt_tokens" "记录应包含 prompt_tokens 字段"
assert_contains "$RECORDS_BODY" "completion_tokens" "记录应包含 completion_tokens 字段"

echo ""

# ============================================================
# 步骤 6: 测试第二个轨迹的完整数据
# ============================================================
log_step "步骤 6: 查询第二个轨迹的完整数据"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TRAJ_API_BASE_URL}/trajectories/${TRAJ_API_SESSION_ID_2}'"
log_separator

RECORDS_RESPONSE_2=$(curl -s -w "\n%{http_code}" -X GET "${TRAJ_API_BASE_URL}/trajectories/${TRAJ_API_SESSION_ID_2}")

RECORDS_BODY_2=$(echo "$RECORDS_RESPONSE_2" | sed '$d')
RECORDS_STATUS_2=$(echo "$RECORDS_RESPONSE_2" | sed -n '$p')

log_response "HTTP Status: ${RECORDS_STATUS_2}"
log_response "${RECORDS_BODY_2}"
log_separator

assert_http_status "200" "$RECORDS_STATUS_2" "HTTP 状态码应为 200"

RESPONSE_SESSION_ID_2=$(json_get "$RECORDS_BODY_2" "session_id")
assert_eq "$TRAJ_API_SESSION_ID_2" "$RESPONSE_SESSION_ID_2" "session_id 应为 ${TRAJ_API_SESSION_ID_2}"

echo ""

# ============================================================
# 步骤 7: 验证旧接口 /trajectory 向后兼容
# ============================================================
log_step "步骤 7: 验证旧接口向后兼容（GET /trajectory）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TRAJ_API_BASE_URL}/trajectory?session_id=${TRAJ_API_SESSION_ID_1}&limit=100'"
log_separator

LEGACY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TRAJ_API_BASE_URL}/trajectory?session_id=${TRAJ_API_SESSION_ID_1}&limit=100")

LEGACY_BODY=$(echo "$LEGACY_RESPONSE" | sed '$d')
LEGACY_STATUS=$(echo "$LEGACY_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${LEGACY_STATUS}"
log_response "${LEGACY_BODY}"
log_separator

assert_http_status "200" "$LEGACY_STATUS" "HTTP 状态码应为 200"

# 验证旧接口响应格式
assert_contains "$LEGACY_BODY" "session_id" "响应应包含 session_id 字段"
assert_contains "$LEGACY_BODY" "count" "响应应包含 count 字段"
assert_contains "$LEGACY_BODY" "records" "响应应包含 records 字段"

# 验证 session_id 匹配
LEGACY_SESSION_ID=$(json_get "$LEGACY_BODY" "session_id")
assert_eq "$TRAJ_API_SESSION_ID_1" "$LEGACY_SESSION_ID" "session_id 应为 ${TRAJ_API_SESSION_ID_1}"

# 验证 count 字段存在且为数字
LEGACY_COUNT=$(json_get_number "$LEGACY_BODY" "count")
if [ -n "$LEGACY_COUNT" ] && [ "$LEGACY_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "旧接口 count 字段正常: ${LEGACY_COUNT}"
else
    log_error "旧接口 count 字段异常: ${LEGACY_COUNT}"
    TEST_FAILED=1
fi

echo ""

# ============================================================
# 步骤 8: 测试不存在的 session_id
# ============================================================
log_step "步骤 8: 测试不存在的 session_id（应返回空记录）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TRAJ_API_BASE_URL}/trajectories/nonexistent-session-id'"
log_separator

NOTEXIST_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TRAJ_API_BASE_URL}/trajectories/nonexistent-session-id")

NOTEXIST_BODY=$(echo "$NOTEXIST_RESPONSE" | sed '$d')
NOTEXIST_STATUS=$(echo "$NOTEXIST_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${NOTEXIST_STATUS}"
log_response "${NOTEXIST_BODY}"
log_separator

assert_http_status "200" "$NOTEXIST_STATUS" "HTTP 状态码应为 200（空记录）"
assert_contains "$NOTEXIST_BODY" "records" "响应应包含 records 字段"

# 验证记录为空
NOTEXIST_RECORDS_COUNT=$(echo "$NOTEXIST_BODY" | grep -o '"unique_id"' | wc -l | tr -d ' ')
if [ "$NOTEXIST_RECORDS_COUNT" -eq 0 ] 2>/dev/null; then
    log_success "不存在的 session 返回空记录列表"
else
    log_error "不存在的 session 应返回空记录，实际有: ${NOTEXIST_RECORDS_COUNT} 条"
    TEST_FAILED=1
fi

echo ""

# ============================================================
# 步骤 9: 删除模型
# ============================================================
log_step "步骤 9: 删除模型（run_id: ${TRAJ_API_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${TRAJ_API_BASE_URL}/models?model_name=${TRAJ_API_MODEL_NAME}&run_id=${TRAJ_API_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${TRAJ_API_BASE_URL}/models?model_name=${TRAJ_API_MODEL_NAME}&run_id=${TRAJ_API_RUN_ID}")

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

# ============================================================
# 打印测试摘要
# ============================================================
print_summary
