#!/bin/bash
# 场景 P310: Record 列表接口测试（Proxy 层）
# 测试 GET /trajectories/{session_id}/records 接口：
#   - 返回 record 元数据列表（含归档记录）
#   - 仅返回轻量元数据（不含 messages/raw_request 等详情大字段）
#   - 不存在的 session 返回空 records 数组

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P310: Record 列表接口测试（Proxy 层）"
echo "========================================"
echo ""

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# ============================================================
# 步骤 1: 注册模型（直接转发模式）
# ============================================================
log_step "步骤 1: 注册模型（run_id: ${TEST_RUN_ID}）"

REGISTER_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

assert_http_status "200" "$REGISTER_STATUS" "注册模型应返回 200"
assert_eq "success" "$(json_get "$REGISTER_BODY" "status")" "注册模型应返回 success"
sleep 0.3
echo ""

# ============================================================
# 步骤 2: 发送推理请求生成轨迹数据
# ============================================================
log_step "步骤 2: 发送推理请求（session: ${TEST_SESSION_ID}）"

CHAT_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello record list test\"}],
        \"stream\": false
    }")

CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')
assert_http_status "200" "$CHAT_STATUS" "推理请求应返回 200"
sleep 0.3
echo ""

# ============================================================
# 步骤 3: 测试 GET /trajectories/{session_id}/records
# ============================================================
log_step "步骤 3: 查询 record 列表（GET /trajectories/{session_id}/records）"

RECORDS_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}/records")

RECORDS_BODY=$(echo "$RECORDS_RESPONSE" | sed '$d')
RECORDS_STATUS=$(echo "$RECORDS_RESPONSE" | sed -n '$p')

assert_http_status "200" "$RECORDS_STATUS" "HTTP 状态码应为 200"

# 验证响应结构
assert_contains "$RECORDS_BODY" "session_id" "响应应包含 session_id 字段"
assert_contains "$RECORDS_BODY" "records" "响应应包含 records 字段"

# 验证 session_id 匹配
RESPONSE_SESSION_ID=$(json_get "$RECORDS_BODY" "session_id")
assert_eq "$TEST_SESSION_ID" "$RESPONSE_SESSION_ID" "session_id 应匹配"

# 验证记录数大于 0
RECORDS_COUNT=$(echo "$RECORDS_BODY" | grep -o '"unique_id"' | wc -l | tr -d ' ')
if [ "$RECORDS_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "record 数量: ${RECORDS_COUNT}，确认记录已获取"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "record 数量应大于 0" "实际: ${RECORDS_COUNT}"
fi

# 验证包含元数据字段
assert_contains "$RECORDS_BODY" "request_id" "应包含 request_id 字段"
assert_contains "$RECORDS_BODY" "model" "应包含 model 字段"
assert_contains "$RECORDS_BODY" "prompt_tokens" "应包含 prompt_tokens 字段"
assert_contains "$RECORDS_BODY" "archive_location" "应包含 archive_location 字段"

# 验证不含详情大字段（核心契约：仅返回轻量元数据）
if echo "$RECORDS_BODY" | grep -q '"messages"'; then
    assert_fail "不应包含详情大字段 'messages'（仅返回元数据）"
else
    log_success "不含 'messages' 字段，确认仅返回元数据"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
fi

if echo "$RECORDS_BODY" | grep -q '"raw_request"'; then
    assert_fail "不应包含详情大字段 'raw_request'（仅返回元数据）"
else
    log_success "不含 'raw_request' 字段，确认仅返回元数据"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
fi

echo ""

# ============================================================
# 步骤 4: 测试不存在的 session_id（应返回空 records 数组）
# ============================================================
log_step "步骤 4: 测试不存在的 session_id（应返回空 records 数组）"

NOTEXIST_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/nonexistent-session-${SCENARIO_ID}/records")

NOTEXIST_BODY=$(echo "$NOTEXIST_RESPONSE" | sed '$d')
NOTEXIST_STATUS=$(echo "$NOTEXIST_RESPONSE" | sed -n '$p')

assert_http_status "200" "$NOTEXIST_STATUS" "不存在的 session 应返回 200"
assert_contains "$NOTEXIST_BODY" "records" "响应应包含 records 字段"

# 验证 records 为空
NOTEXIST_RECORDS_COUNT=$(echo "$NOTEXIST_BODY" | grep -o '"unique_id"' | wc -l | tr -d ' ')
if [ "$NOTEXIST_RECORDS_COUNT" -eq 0 ] 2>/dev/null; then
    log_success "不存在的 session 返回空 records 数组"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "不存在的 session 应返回空 records" "实际: ${NOTEXIST_RECORDS_COUNT} 条"
fi

echo ""

# ============================================================
# 步骤 5: 清理 — 删除模型
# ============================================================
log_step "步骤 5: 删除模型"

DELETE_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

assert_http_status "200" "$DELETE_STATUS" "删除模型应返回 200"
assert_eq "success" "$(json_get "$DELETE_BODY" "status")" "删除模型应返回 success"

echo ""

# ============================================================
# 打印测试摘要
# ============================================================
print_summary
