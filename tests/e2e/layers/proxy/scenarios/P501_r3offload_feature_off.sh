#!/bin/bash
# 场景 P501: route_experts 卸载 — feature on 客户端零影响（Proxy 层）
#
# 验证要点：
#   1. feature on 时客户端响应不含 routed_experts（proxy 剥离，对客户端零影响）
#   2. 轨迹记录的 routed_experts 为 marker（feature 生效，已卸载到 blob）
#   3. GET /trajectories/{session_id}/records/{request_id}/route_experts 返回 200 + 原始数据（fallback 回拉成功）
#
# 前置条件: Worker 以 route_experts_offload.enabled=true, backend=local 启动

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P501: route_experts 卸载 — feature on 客户端零影响"
echo "========================================"
echo ""

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="r3offload-off-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

MOCK_PORT=19990
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_PID=""
MOCK_INFER_URL="http://${MOCK_INFER_HOST}:${MOCK_PORT}/v1"

trap stop_mock EXIT

# ========================================
# 步骤 0: 清理残留模型
# ========================================
log_info "清理可能残留的模型..."
curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1 || true

# ========================================
# 步骤 1: 启动Mock推理服务
# ========================================
log_step "步骤 1: 启动Mock推理服务"

if ! start_mock; then
    log_error "无法启动Mock服务，测试终止"
    exit 1
fi
echo ""

# ========================================
# 步骤 2: 注册模型（直接转发模式）
# ========================================
log_step "步骤 2: 注册模型（直接转发模式, token_in_token_out: false）"

REGISTER_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
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

assert_http_status "200" "$REGISTER_STATUS" "注册模型应返回 200"
assert_eq "success" "$(json_get "$REGISTER_BODY" "status")" "注册模型应返回 success"

sleep 0.5
echo ""

# ========================================
# 步骤 3: 发送非流式推理请求
# ========================================
log_step "步骤 3: 发送非流式推理请求"

CHAT_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

assert_http_status "200" "$CHAT_STATUS" "推理请求应返回 200"
sleep 0.3
echo ""

# ========================================
# 步骤 4: 验证客户端响应已剥离 routed_experts
# ========================================
log_step "步骤 4: 验证客户端响应已剥离 routed_experts（proxy 不返回客户端）"

RE_CHECK=$(echo "$CHAT_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
choices = data.get('choices', [])
if not choices:
    print('FAIL:无 choices')
    sys.exit(0)
# proxy 剥离 routed_experts，客户端响应不应含此字段（也不应含 marker）
if 'routed_experts' in choices[0]:
    print('FAIL:客户端响应不应含 routed_experts（proxy 应剥离）')
else:
    print('PASS:客户端响应已剥离 routed_experts')
" 2>/dev/null)

if echo "$RE_CHECK" | grep -q "^PASS:"; then
    log_success "$RE_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "$RE_CHECK"
fi
echo ""

# ========================================
# 步骤 5: 提取 request_id 并验证轨迹 routed_experts 为 marker
# ========================================
log_step "步骤 5: 从轨迹列表提取 request_id 并验证 routed_experts 为 marker"

TRAJ_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}?limit=1")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

assert_http_status "200" "$TRAJ_STATUS" "轨迹查询应返回 200"

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

# 查询轨迹详情，验证 routed_experts 为 marker —— feature on 的核心断言
DETAIL_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}/records/${REQUEST_ID}?fields=raw_response")

DETAIL_BODY=$(echo "$DETAIL_RESPONSE" | sed '$d')
DETAIL_STATUS=$(echo "$DETAIL_RESPONSE" | sed -n '$p')

assert_http_status "200" "$DETAIL_STATUS" "record 详情查询应返回 200"

DB_RE_CHECK=$(echo "$DETAIL_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
raw_response = data.get('raw_response')
if not raw_response:
    print('FAIL:无 raw_response 字段')
    sys.exit(0)
if isinstance(raw_response, str):
    try:
        raw_response = json.loads(raw_response)
    except json.JSONDecodeError:
        print('FAIL:raw_response JSON 解析失败')
        sys.exit(0)
choices = raw_response.get('choices', [])
if not choices:
    print('FAIL:raw_response.choices 为空')
    sys.exit(0)
re = choices[0].get('routed_experts')
if re is None:
    print('FAIL:轨迹记录无 routed_experts 字段')
elif isinstance(re, dict) and re.get('_offloaded') is True:
    path = re.get('location', {}).get('path', '')
    backend = re.get('backend', '')
    print(f'PASS:routed_experts 是 marker, backend={backend}, path={path}')
elif isinstance(re, list):
    print('FAIL:routed_experts 是原始数组，feature 可能未开启（本场景要求 feature on）')
else:
    print(f'FAIL:routed_experts 类型异常: {type(re).__name__}')
" 2>/dev/null)

if echo "$DB_RE_CHECK" | grep -q "^PASS:"; then
    log_success "$DB_RE_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "$DB_RE_CHECK"
fi
echo ""

# ========================================
# 步骤 6: 验证 fallback 端点返回 200 + 原始 routed_experts
# ========================================
log_step "步骤 6: 验证 GET .../route_experts 返回 200（feature on 回拉成功）"

R3_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}/records/${REQUEST_ID}/route_experts")

R3_BODY=$(echo "$R3_RESPONSE" | sed '$d')
R3_STATUS=$(echo "$R3_RESPONSE" | sed -n '$p')

assert_http_status "200" "$R3_STATUS" "feature on 时 fallback 端点应返回 200"
assert_contains "$R3_BODY" "expert_id" "200 响应应包含原始 routed_experts (expert_id)"
assert_contains "$R3_BODY" "weight" "200 响应应包含 weight 字段"
echo ""

# ========================================
# 步骤 7: 清理 — 删除模型
# ========================================
log_step "步骤 7: 删除模型"

DELETE_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

assert_http_status "200" "$DELETE_STATUS" "删除模型应返回 200"
assert_eq "success" "$(json_get "$DELETE_BODY" "status")" "删除模型应返回 success"
echo ""

print_summary
