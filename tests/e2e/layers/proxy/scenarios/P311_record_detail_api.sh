#!/bin/bash
# 场景 P311: Record 单条详情接口测试（Proxy 层）
# 测试 GET /trajectories/{session_id}/records/{request_id} 接口：
#   - 返回单条 record 完整详情
#   - 活跃记录详情字段非空
#   - 不存在的 request_id 返回 404

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P311: Record 单条详情接口测试（Proxy 层）"
echo "========================================"
echo ""

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# ============================================================
# 步骤 1: 注册模型
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
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello record detail test\"}],
        \"stream\": false
    }")

CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')
assert_http_status "200" "$CHAT_STATUS" "推理请求应返回 200"
sleep 0.3
echo ""

# ============================================================
# 步骤 3: 从轨迹列表提取 request_id
# ============================================================
log_step "步骤 3: 从 GET /trajectories/{session_id} 提取 request_id"

TRAJ_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?limit=1")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

assert_http_status "200" "$TRAJ_STATUS" "轨迹查询应返回 200"

# 提取第一条记录的 request_id
REQUEST_ID=$(echo "$TRAJ_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
records = data.get('records', [])
if records:
    print(records[0].get('request_id', ''))
" 2>/dev/null)

if [ -n "$REQUEST_ID" ]; then
    log_success "提取到 request_id: ${REQUEST_ID}"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "未能从轨迹记录中提取 request_id"
fi
echo ""

# ============================================================
# 步骤 4: 测试 GET /trajectories/{session_id}/records/{request_id}
# ============================================================
log_step "步骤 4: 查询单条 record 详情"

DETAIL_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}/records/${REQUEST_ID}")

DETAIL_BODY=$(echo "$DETAIL_RESPONSE" | sed '$d')
DETAIL_STATUS=$(echo "$DETAIL_RESPONSE" | sed -n '$p')

assert_http_status "200" "$DETAIL_STATUS" "单条详情查询应返回 200"

# 验证标识字段匹配
assert_eq "$TEST_SESSION_ID" "$(json_get "$DETAIL_BODY" "session_id")" "session_id 应匹配"
assert_eq "$REQUEST_ID" "$(json_get "$DETAIL_BODY" "request_id")" "request_id 应匹配"
assert_contains "$DETAIL_BODY" "${TEST_SESSION_ID},${REQUEST_ID}" "unique_id 应为 {session_id},{request_id}"

# 验证元数据字段正常
assert_contains "$DETAIL_BODY" "model" "应包含 model 字段"
assert_contains "$DETAIL_BODY" "prompt_tokens" "应包含 prompt_tokens 字段"
assert_contains "$DETAIL_BODY" "start_time" "应包含 start_time 字段"

# 验证详情字段非空 + archive_location 为 null（核心契约）
DETAIL_CROSSCHECK=$(echo "$DETAIL_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
errors = []

messages = data.get('messages')
if not messages or not isinstance(messages, list) or len(messages) == 0:
    errors.append('messages 应为非空数组')

raw_request = data.get('raw_request')
if not raw_request:
    errors.append('raw_request 应非空')
elif isinstance(raw_request, dict):
    if 'messages' not in raw_request:
        errors.append('raw_request 应包含 messages 子字段')

archive_location = data.get('archive_location')
if archive_location is not None:
    errors.append(f'archive_location 应为 null（活跃记录），实际: {archive_location}')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    msg_len = len(messages) if messages else 0
    print(f'PASS:messages长度={msg_len}, raw_request含messages, archive_location=null')
" 2>/dev/null)

if echo "$DETAIL_CROSSCHECK" | grep -q "^PASS:"; then
    log_success "$DETAIL_CROSSCHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "$DETAIL_CROSSCHECK"
fi
echo ""

# ============================================================
# 步骤 5: 测试不存在的 request_id（应返回 404）
# ============================================================
log_step "步骤 5: 测试不存在的 request_id（应返回 404）"

NOTFOUND_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}/records/nonexistent-uuid-xxxx")

NOTFOUND_STATUS=$(echo "$NOTFOUND_RESPONSE" | sed -n '$p')

assert_http_status "404" "$NOTFOUND_STATUS" "不存在的 request_id 应返回 404"

echo ""

# ============================================================
# 步骤 6: 清理 — 删除模型
# ============================================================
log_step "步骤 6: 删除模型"

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
