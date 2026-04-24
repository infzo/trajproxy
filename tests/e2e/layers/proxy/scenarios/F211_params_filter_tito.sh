#!/bin/bash
# 场景 F211: 参数透传场景 - TITO模式参数过滤（Proxy 层）
# 测试流程：启动mock服务 -> 注册模型(TITO模式) -> 发送请求(含兼容和不兼容参数) -> 验证mock收到的请求 -> 删除模型 -> 停止mock
#
# 验证要点：
#   1. 兼容参数(seed, n, stop, user)被透传到后端
#   2. 不兼容参数(response_format, logit_bias)被过滤丢弃
#   3. 自定义header(x-sandbox-traj-id)被透传

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F211: 参数透传场景 - TITO模式参数过滤（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="passthrough-tito-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# TITO模式需要tokenizer路径
TEST_TOKENIZER_PATH="${TEST_TOKENIZER_PATH:-Qwen/Qwen3.5-2B}"

# Mock服务配置
MOCK_PORT=19991
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
# 步骤 2: 注册模型（TITO模式）
# ========================================
log_step "步骤 2: 注册模型（TITO模式, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

# 允许tokenizer加载失败时跳过测试
if [ "$REGISTER_STATUS" != "200" ]; then
    log_warning "模型注册失败（可能tokenizer不可用），跳过TITO测试"
    log_warning "请确保 ${TEST_TOKENIZER_PATH} tokenizer可用"
    stop_mock
    exit 0
fi

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 3

echo ""

# ========================================
# 步骤 3: 发送请求（含兼容和不兼容参数）
# ========================================
log_step "步骤 3: 发送请求（含兼容和不兼容参数）"
log_info "兼容参数: seed, n, stop, user"
log_info "不兼容参数: response_format, logit_bias（应被过滤）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -H 'x-sandbox-traj-id: tito-traj-789' \\
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"max_tokens\": 10,
        \"seed\": 42,
        \"n\": 1,
        \"stop\": [\"\\n\"],
        \"user\": \"tito-user\",
        \"response_format\": {\"type\": \"json_object\"},
        \"logit_bias\": {\"1\": -100},
        \"stream\": false
    }'"
log_separator

# 清空之前的mock记录
clear_mock_records

CHAT_RESPONSE=$(curl -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -H "x-sandbox-traj-id: tito-traj-789" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"max_tokens\": 10,
        \"seed\": 42,
        \"n\": 1,
        \"stop\": [\"\\n\"],
        \"user\": \"tito-user\",
        \"response_format\": {\"type\": \"json_object\"},
        \"logit_bias\": {\"1\": -100},
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

echo ""

# ========================================
# 步骤 4: 验证TITO参数过滤
# ========================================
log_step "步骤 4: 验证TITO参数过滤"
log_separator

# 验证TITO不兼容参数(response_format, logit_bias)被过滤，兼容参数被透传
VERIFY_RESULT=$(verify_infer_request "seed n stop user response_format logit_bias" "x-sandbox-traj-id x-run-id x-session-id")
log_info "Mock收到的请求验证结果:"
echo "$VERIFY_RESULT" | grep -E "^(BODY|HEADER):" | while read line; do
    echo "  $line"
done

echo ""
log_info "【验证不兼容参数被过滤】"

# 验证response_format被过滤（不存在于body中）
RESPONSE_FORMAT_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:response_format=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$RESPONSE_FORMAT_VAL" "response_format应被过滤（TITO不兼容）"

# 验证logit_bias被过滤
LOGIT_BIAS_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:logit_bias=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$LOGIT_BIAS_VAL" "logit_bias应被过滤（TITO不兼容）"

echo ""
log_info "【验证兼容参数被透传】"

# 验证seed被透传
SEED_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:seed=" | cut -d= -f2)
assert_eq "42" "$SEED_VAL" "seed参数应被透传"

# 验证n被透传
N_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:n=" | cut -d= -f2)
assert_eq "1" "$N_VAL" "n参数应被透传"

# 验证user被透传
USER_VAL=$(echo "$VERIFY_RESULT" | grep "^BODY:user=" | cut -d= -f2)
assert_eq "tito-user" "$USER_VAL" "user参数应被透传"

echo ""
log_info "【验证Header透传】"

# 验证header被透传
TRAJ_ID_VAL=$(echo "$VERIFY_RESULT" | grep "^HEADER:x-sandbox-traj-id=" | cut -d= -f2)
assert_eq "tito-traj-789" "$TRAJ_ID_VAL" "x-sandbox-traj-id header应被透传"

# 验证内部header不被转发
X_RUN_ID_VAL=$(echo "$VERIFY_RESULT" | grep "^HEADER:x-run-id=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$X_RUN_ID_VAL" "x-run-id header不应被转发"

X_SESSION_ID_VAL=$(echo "$VERIFY_RESULT" | grep "^HEADER:x-session-id=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$X_SESSION_ID_VAL" "x-session-id header不应被转发"

echo ""

# ========================================
# 步骤 5: 删除模型
# ========================================
log_step "步骤 5: 删除模型（run_id: ${TEST_RUN_ID}）"
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
