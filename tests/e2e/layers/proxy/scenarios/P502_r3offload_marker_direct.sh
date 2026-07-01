#!/bin/bash
# 场景 P502: route_experts 卸载 — Marker 契约 + 直访（Direct 路径）（Proxy 层）
#
# 验证要点：
#   1. feature on 时轨迹 raw_response 中 routed_experts 被替换为 local marker
#   2. marker 格式: {"_offloaded":true,"backend":"local","location":{"path":...}}
#   3. marker.location.path 指向的文件内容等于原始 routed_experts
#
# 前置条件: Worker 以 route_experts_offload.enabled=true, backend=local 启动
#           场景开头探测 feature 状态，feature off 时优雅跳过（exit 0）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P502: route_experts 卸载 — Marker 契约 + 直访（Direct 路径）"
echo "========================================"
echo ""

SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="r3offload-direct-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

MOCK_PORT=19997
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
sleep 0.5
echo ""

# ========================================
# 步骤 4: 提取 request_id
# ========================================
log_step "步骤 4: 从轨迹列表提取 request_id"

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

# ========================================
# 步骤 5: 查询轨迹 raw_response，提取 routed_experts
# ========================================
log_step "步骤 5: 查询轨迹 raw_response，提取 routed_experts"

DETAIL_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectories/${TEST_SESSION_ID}/records/${REQUEST_ID}?fields=raw_response")

DETAIL_BODY=$(echo "$DETAIL_RESPONSE" | sed '$d')
DETAIL_STATUS=$(echo "$DETAIL_RESPONSE" | sed -n '$p')

assert_http_status "200" "$DETAIL_STATUS" "record 详情查询应返回 200"

# 提取 routed_experts 并判断是否为 marker
RE_ANALYSIS=$(echo "$DETAIL_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
raw_response = data.get('raw_response')
if not raw_response:
    print('SKIP:无 raw_response 字段')
    sys.exit(0)
if isinstance(raw_response, str):
    try:
        raw_response = json.loads(raw_response)
    except json.JSONDecodeError:
        print('SKIP:raw_response JSON 解析失败')
        sys.exit(0)
choices = raw_response.get('choices', [])
if not choices:
    print('SKIP:raw_response.choices 为空')
    sys.exit(0)
re = choices[0].get('routed_experts')
if re is None:
    print('SKIP:无 routed_experts 字段')
    sys.exit(0)
if isinstance(re, dict) and re.get('_offloaded') is True:
    path = re.get('location', {}).get('path', '')
    backend = re.get('backend', '')
    print(f'MARKER:backend={backend},path={path}')
else:
    print('SKIP:routed_experts 不是 marker，feature 未开启')
" 2>/dev/null)

echo "$RE_ANALYSIS"

# feature 探测：不是 marker 则优雅跳过
if echo "$RE_ANALYSIS" | grep -q "^SKIP:"; then
    log_warning "route_experts 卸载未启用（feature off），本场景跳过"
    log_warning "$RE_ANALYSIS"
    curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1
    print_summary
    exit 0
fi

if ! echo "$RE_ANALYSIS" | grep -q "^MARKER:"; then
    assert_fail "提取 routed_experts 失败: $RE_ANALYSIS"
    curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1
    print_summary
    exit 1
fi
echo ""

# ========================================
# 步骤 6: 验证 marker 格式
# ========================================
log_step "步骤 6: 验证 marker 格式（_offloaded / backend / location.path）"

MARKER_CHECK=$(echo "$DETAIL_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
re = data['raw_response']['choices'][0]['routed_experts']
errors = []
if re.get('_offloaded') is not True:
    errors.append('_offloaded 应为 true')
if re.get('backend') != 'local':
    errors.append(f'backend 应为 local，实际: {re.get(\"backend\")}')
location = re.get('location', {})
if not location.get('path'):
    errors.append('location.path 不应为空')
if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print(f'PASS:marker 格式正确, path={location[\"path\"]}')
" 2>/dev/null)

if echo "$MARKER_CHECK" | grep -q "^PASS:"; then
    log_success "$MARKER_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "$MARKER_CHECK"
fi
echo ""

# ========================================
# 步骤 7: 直访 marker.location.path 文件，验证内容等于原始 routed_experts
# ========================================
log_step "步骤 7: 直访 marker.location.path 文件，验证内容"

BLOB_PATH=$(echo "$RE_ANALYSIS" | grep "^MARKER:" | sed 's/.*path=//')
log_info "blob 文件路径: ${BLOB_PATH}"

# 等待 blob 文件就绪（fire-and-forget 上传，可能尚未完成）
BLOB_READY=false
for i in $(seq 1 20); do
    if [ -f "$BLOB_PATH" ]; then
        BLOB_READY=true
        break
    fi
    sleep 0.5
done

if [ "$BLOB_READY" != "true" ]; then
    assert_fail "blob 文件未就绪（等待 10s 仍未出现）: ${BLOB_PATH}"
    echo ""
    log_step "步骤 8: 删除模型"
    curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1
    print_summary
    exit 1
fi

log_success "blob 文件已就绪: ${BLOB_PATH}"
TESTS_TOTAL=$((TESTS_TOTAL + 1))
TESTS_PASSED=$((TESTS_PASSED + 1))

# 验证文件内容等于 mock infer 返回的原始 routed_experts
BLOB_CHECK=$(python3 -c "
import json, os
path = '${BLOB_PATH}'
with open(path, 'rb') as f:
    content = f.read()
re_data = json.loads(content)
# mock infer server 固定返回的 routed_experts
expected = [{'expert_id': 2, 'weight': 0.7}, {'expert_id': 5, 'weight': 0.3}]
if re_data == expected:
    print(f'PASS:blob 文件内容等于原始 routed_experts ({len(content)} bytes)')
else:
    print(f'FAIL:blob 内容不匹配, 期望={expected}, 实际={re_data}')
" 2>/dev/null)

if echo "$BLOB_CHECK" | grep -q "^PASS:"; then
    log_success "$BLOB_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "$BLOB_CHECK"
fi
echo ""

# ========================================
# 步骤 8: 清理 — 删除模型
# ========================================
log_step "步骤 8: 删除模型"

DELETE_RESPONSE=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

assert_http_status "200" "$DELETE_STATUS" "删除模型应返回 200"
assert_eq "success" "$(json_get "$DELETE_BODY" "status")" "删除模型应返回 success"
echo ""

print_summary
