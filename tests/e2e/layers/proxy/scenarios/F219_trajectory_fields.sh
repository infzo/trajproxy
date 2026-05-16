#!/bin/bash
# 场景 F219: 轨迹查询 fields 参数测试（Proxy 层）
# 测试 fields 查询参数支持：
#   - 包含语法: ?fields=model,prompt_tokens（只返回指定字段）
#   - 排除语法: ?fields=-raw_request,-raw_response（排除指定字段）
#   - 默认行为: 不传 fields 返回全部字段
#   - 新接口 GET /trajectories/{session_id}?fields=...
#   - 旧接口 GET /trajectory?session_id=...&fields=...

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F219: 轨迹查询 fields 参数测试（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="traj-fields-test-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# ============================================================
# 步骤 1: 注册模型（直接转发模式）
# ============================================================
log_step "步骤 1: 注册模型（run_id: ${TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
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

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "注册模型应返回 200"
assert_eq "success" "$(json_get "$REGISTER_BODY" "status")" "注册模型应返回 success"
sleep 1
echo ""

# ============================================================
# 步骤 2: 发送推理请求生成轨迹数据
# ============================================================
log_step "步骤 2: 发送推理请求（session: ${TEST_SESSION_ID}）"
log_separator

CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello, this is a fields test\"}],
        \"stream\": false
    }")

CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')
assert_http_status "200" "$CHAT_STATUS" "推理请求应返回 200"
sleep 1
echo ""

# ============================================================
# 步骤 3: 测试 fields 包含语法（新接口）
# ============================================================
log_step "步骤 3: 新接口 fields 包含语法 — 只返回 model, request_id, prompt_tokens"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?fields=model,request_id,prompt_tokens'"
log_separator

FIELDS_INCLUDE_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?fields=model,request_id,prompt_tokens")

FIELDS_INCLUDE_BODY=$(echo "$FIELDS_INCLUDE_RESPONSE" | sed '$d')
FIELDS_INCLUDE_STATUS=$(echo "$FIELDS_INCLUDE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${FIELDS_INCLUDE_STATUS}"
log_response "${FIELDS_INCLUDE_BODY}"
log_separator

assert_http_status "200" "$FIELDS_INCLUDE_STATUS" "fields include 请求应返回 200"
assert_eq "$TEST_SESSION_ID" "$(json_get "$FIELDS_INCLUDE_BODY" "session_id")" "session_id 应匹配"

# 验证：records 数组中第一个记录应包含 model 字段
RECORD_MODEL_COUNT=$(echo "$FIELDS_INCLUDE_BODY" | grep -o '"model"' | wc -l | tr -d ' ')
if [ "$RECORD_MODEL_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "fields include: 'model' 字段存在于记录中 (${RECORD_MODEL_COUNT} 处匹配)"
else
    log_error "fields include: 'model' 字段应存在于记录中"
    TEST_FAILED=1
fi

# 验证：应包含 request_id
assert_contains "$FIELDS_INCLUDE_BODY" "request_id" "fields include: 应包含 request_id"

# 验证：应包含 prompt_tokens
assert_contains "$FIELDS_INCLUDE_BODY" "prompt_tokens" "fields include: 应包含 prompt_tokens"

# 验证：不应包含未被请求的大字段 messages
if echo "$FIELDS_INCLUDE_BODY" | grep -q '"messages"'; then
    log_error "fields include: 不应包含未请求的 'messages' 字段"
    TEST_FAILED=1
else
    log_success "fields include: 'messages' 字段已被正确排除"
fi

# 验证：不应包含 raw_request
if echo "$FIELDS_INCLUDE_BODY" | grep -q '"raw_request"'; then
    log_error "fields include: 不应包含未请求的 'raw_request' 字段"
    TEST_FAILED=1
else
    log_success "fields include: 'raw_request' 字段已被正确排除"
fi

echo ""

# ============================================================
# 步骤 4: 测试 fields 排除语法（新接口）
# ============================================================
log_step "步骤 4: 新接口 fields 排除语法 — 排除 raw_request, raw_response, messages"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?fields=-raw_request,-raw_response,-messages'"
log_separator

FIELDS_EXCLUDE_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?fields=-raw_request,-raw_response,-messages")

FIELDS_EXCLUDE_BODY=$(echo "$FIELDS_EXCLUDE_RESPONSE" | sed '$d')
FIELDS_EXCLUDE_STATUS=$(echo "$FIELDS_EXCLUDE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${FIELDS_EXCLUDE_STATUS}"
log_response "${FIELDS_EXCLUDE_BODY}"
log_separator

assert_http_status "200" "$FIELDS_EXCLUDE_STATUS" "fields exclude 请求应返回 200"

# 验证：排除了 raw_request
if echo "$FIELDS_EXCLUDE_BODY" | grep -q '"raw_request"'; then
    log_error "fields exclude: 不应包含 'raw_request' 字段"
    TEST_FAILED=1
else
    log_success "fields exclude: 'raw_request' 已被正确排除"
fi

# 验证：排除了 raw_response
if echo "$FIELDS_EXCLUDE_BODY" | grep -q '"raw_response"'; then
    log_error "fields exclude: 不应包含 'raw_response' 字段"
    TEST_FAILED=1
else
    log_success "fields exclude: 'raw_response' 已被正确排除"
fi

# 验证：排除了 messages
if echo "$FIELDS_EXCLUDE_BODY" | grep -q '"messages"'; then
    log_error "fields exclude: 不应包含 'messages' 字段"
    TEST_FAILED=1
else
    log_success "fields exclude: 'messages' 已被正确排除"
fi

# 验证：仍包含未排除的字段 model
assert_contains "$FIELDS_EXCLUDE_BODY" "model" "fields exclude: 仍应包含 model 字段"

# 验证：仍包含 prompt_tokens
assert_contains "$FIELDS_EXCLUDE_BODY" "prompt_tokens" "fields exclude: 仍应包含 prompt_tokens 字段"

echo ""

# ============================================================
# 步骤 5: 测试不传 fields 返回全部字段（新接口）
# ============================================================
log_step "步骤 5: 新接口不传 fields — 应返回全部字段"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?limit=1'"
log_separator

FIELDS_ALL_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?limit=1")

FIELDS_ALL_BODY=$(echo "$FIELDS_ALL_RESPONSE" | sed '$d')
FIELDS_ALL_STATUS=$(echo "$FIELDS_ALL_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${FIELDS_ALL_STATUS}"
log_response "${FIELDS_ALL_BODY}"
log_separator

assert_http_status "200" "$FIELDS_ALL_STATUS" "不传 fields 请求应返回 200"

# 验证：全量查询应包含大字段 messages
assert_contains "$FIELDS_ALL_BODY" "messages" "默认全字段: 应包含 messages"

# 验证：全量查询应包含 raw_request
assert_contains "$FIELDS_ALL_BODY" "raw_request" "默认全字段: 应包含 raw_request"

# 验证：全量查询应包含 raw_response
assert_contains "$FIELDS_ALL_BODY" "raw_response" "默认全字段: 应包含 raw_response"

# 验证：全量查询应包含核心字段
assert_contains "$FIELDS_ALL_BODY" "model" "默认全字段: 应包含 model"
assert_contains "$FIELDS_ALL_BODY" "prompt_tokens" "默认全字段: 应包含 prompt_tokens"
assert_contains "$FIELDS_ALL_BODY" "completion_tokens" "默认全字段: 应包含 completion_tokens"

echo ""

# ============================================================
# 步骤 6: 测试旧接口 fields 参数向后兼容
# ============================================================
log_step "步骤 6: 旧接口 /trajectory 支持 fields 参数"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&fields=model,session_id,request_id&limit=1'"
log_separator

LEGACY_FIELDS_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X GET "${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&fields=model,session_id,request_id&limit=1")

LEGACY_FIELDS_BODY=$(echo "$LEGACY_FIELDS_RESPONSE" | sed '$d')
LEGACY_FIELDS_STATUS=$(echo "$LEGACY_FIELDS_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${LEGACY_FIELDS_STATUS}"
log_response "${LEGACY_FIELDS_BODY}"
log_separator

assert_http_status "200" "$LEGACY_FIELDS_STATUS" "旧接口 fields 请求应返回 200"

# 验证旧接口响应结构
assert_contains "$LEGACY_FIELDS_BODY" "session_id" "旧接口: 应包含 session_id"
assert_contains "$LEGACY_FIELDS_BODY" "count" "旧接口: 应包含 count"
assert_contains "$LEGACY_FIELDS_BODY" "records" "旧接口: 应包含 records"

# 验证请求的字段存在于记录中
assert_contains "$LEGACY_FIELDS_BODY" "model" "旧接口 fields: 应包含 model"
assert_contains "$LEGACY_FIELDS_BODY" "request_id" "旧接口 fields: 应包含 request_id"

# 验证未请求的大字段不应存在
if echo "$LEGACY_FIELDS_BODY" | grep -q '"messages"'; then
    log_error "旧接口 fields: 不应包含未请求的 'messages' 字段"
    TEST_FAILED=1
else
    log_success "旧接口 fields: 'messages' 已被正确排除"
fi

echo ""

# ============================================================
# 步骤 7: 清理 — 删除模型和 Mock 服务
# ============================================================
log_step "步骤 7: 删除模型"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "删除模型应返回 200"
assert_eq "success" "$(json_get "$DELETE_BODY" "status")" "删除模型应返回 success"

echo ""

# ============================================================
# 打印测试摘要
# ============================================================
print_summary
