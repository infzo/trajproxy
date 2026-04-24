#!/bin/bash
# 场景 F210: 参数透传场景 - 直接转发模式（Proxy 层）
# 测试流程：启动mock服务 -> 注册模型(直接模式) -> 发送请求(带扩展参数和header) -> 验证mock收到的请求 -> 删除模型 -> 停止mock
#
# 验证要点：
#   1. OpenAI 标准参数(n, seed, stop, logprobs, top_logprobs, user)被透传到后端
#   2. 自定义header(x-sandbox-traj-id)被透传
#   3. 内部header(x-run-id, x-session-id, authorization)不被转发

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F210: 参数透传场景 - 直接转发模式（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="passthrough-direct-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# Mock服务配置
MOCK_PORT=19990
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_PID=""
MOCK_INFER_URL="http://${MOCK_INFER_HOST}:${MOCK_PORT}/v1"

# 确保退出时停止mock服务
trap stop_mock EXIT

# ========================================
# 步骤 0: 清理残留模型
# ========================================
log_info "清理可能残留的模型..."
curl -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}" > /dev/null 2>&1

# ========================================
# 步骤 1: 启动Mock服务
# ========================================
log_step "步骤 1: 启动Mock推理服务"
log_separator

if ! start_mock; then
    log_error "无法启动Mock服务，测试终止"
    exit 1
fi

echo ""

# ========================================
# 步骤 2: 注册模型（直接转发模式）
# ========================================
log_step "步骤 2: 注册模型（直接转发模式, token_in_token_out: false）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
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

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 2

echo ""

# ========================================
# 步骤 3: 发送非流式请求（带扩展参数和自定义header）
# ========================================
log_step "步骤 3: 发送非流式请求（带扩展参数和自定义header）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -H 'x-sandbox-traj-id: test-traj-123' \\
    -H 'x-custom-header: custom-value' \\
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hello.\"}],
        \"max_tokens\": 10,
        \"temperature\": 0.0,
        \"seed\": 42,
        \"n\": 1,
        \"stop\": [\"\\n\"],
        \"logprobs\": true,
        \"top_logprobs\": 3,
        \"user\": \"test-user-passthrough\",
        \"service_tier\": \"auto\",
        \"stream\": false
    }'"
log_separator

# 清空之前的mock记录
clear_mock_records

CHAT_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -H "x-sandbox-traj-id: test-traj-123" \
    -H "x-custom-header: custom-value" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hello.\"}],
        \"max_tokens\": 10,
        \"temperature\": 0.0,
        \"seed\": 42,
        \"n\": 1,
        \"stop\": [\"\\n\"],
        \"logprobs\": true,
        \"top_logprobs\": 3,
        \"user\": \"test-user-passthrough\",
        \"service_tier\": \"auto\",
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

echo ""

# ========================================
# 步骤 4: 验证参数透传（非流式）
# ========================================
log_step "步骤 4: 验证参数透传（非流式请求）"
log_separator

VERIFY_RESULT=$(verify_infer_request "seed n stop logprobs top_logprobs user service_tier" "x-sandbox-traj-id x-custom-header x-run-id x-session-id")
log_info "Mock收到的请求验证结果:"
echo "$VERIFY_RESULT" | grep -E "^(BODY|HEADER):" | while read line; do
    echo "  $line"
done

# 验证参数透传
SEED_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:seed=" | cut -d= -f2)
assert_eq "42" "$SEED_VAL" "seed参数应被透传，期望42"

N_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:n=" | cut -d= -f2)
assert_eq "1" "$N_VAL" "n参数应被透传，期望1"

LOGPROBS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logprobs=" | cut -d= -f2)
assert_eq "True" "$LOGPROBS_VAL" "logprobs参数应被透传"

USER_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:user=" | cut -d= -f2)
assert_eq "test-user-passthrough" "$USER_VAL" "user参数应被透传"

# 验证header透传
TRAJ_ID_VAL=$(echo "$VERIFY_RESULT" | grep "^HEADER:x-sandbox-traj-id=" | cut -d= -f2)
assert_eq "test-traj-123" "$TRAJ_ID_VAL" "x-sandbox-traj-id header应被透传"

CUSTOM_VAL=$(echo "$VERIFY_RESULT" | grep "^HEADER:x-custom-header=" | cut -d= -f2)
assert_eq "custom-value" "$CUSTOM_VAL" "x-custom-header应被透传"

# 验证内部header不被转发
X_RUN_ID_VAL=$(echo "$VERIFY_RESULT" | grep "^HEADER:x-run-id=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$X_RUN_ID_VAL" "x-run-id header不应被转发"

X_SESSION_ID_VAL=$(echo "$VERIFY_RESULT" | grep "^HEADER:x-session-id=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$X_SESSION_ID_VAL" "x-session-id header不应被转发"

echo ""

# ========================================
# 步骤 5: 发送流式请求（验证参数和header透传）
# ========================================
log_step "步骤 5: 发送流式请求（验证参数和header透传）"
log_curl_cmd "curl -s --no-buffer \\
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -H 'x-sandbox-traj-id: stream-traj-456' \\
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hi.\"}],
        \"max_tokens\": 10,
        \"seed\": 99,
        \"stream\": true
    }'"
log_separator

# 清空之前的mock记录
clear_mock_records

STREAM_RESPONSE=$(curl -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-stream/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -H "x-sandbox-traj-id: stream-traj-456" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say hi.\"}],
        \"max_tokens\": 10,
        \"seed\": 99,
        \"stream\": true
    }")

log_info "流式响应内容（截取前5行）:"
echo "$STREAM_RESPONSE" | head -5
log_separator

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

echo ""

# ========================================
# 步骤 6: 验证参数透传（流式）
# ========================================
log_step "步骤 6: 验证参数透传（流式请求）"
log_separator

VERIFY_RESULT_STREAM=$(verify_infer_request "seed" "x-sandbox-traj-id")
log_info "Mock收到的流式请求验证结果:"
echo "$VERIFY_RESULT_STREAM" | grep -E "^(BODY|HEADER):" | while read line; do
    echo "  $line"
done

# 验证流式参数透传
SEED_STREAM_VAL=$(echo "$VERIFY_RESULT_STREAM" | grep "^BODY:seed=" | cut -d= -f2)
assert_eq "99" "$SEED_STREAM_VAL" "流式请求seed参数应被透传，期望99"

# 验证流式header透传
TRAJ_ID_STREAM_VAL=$(echo "$VERIFY_RESULT_STREAM" | grep "^HEADER:x-sandbox-traj-id=" | cut -d= -f2)
assert_eq "stream-traj-456" "$TRAJ_ID_STREAM_VAL" "流式请求x-sandbox-traj-id header应被透传"

echo ""

# ========================================
# 步骤 7: 删除模型
# ========================================
log_step "步骤 7: 删除模型（run_id: ${TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

# 打印测试摘要
print_summary
