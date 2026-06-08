#!/bin/bash
# 场景 P204: chat_template_kwargs 透传与消费测试（Proxy 层）
# 测试流程：启动mock服务 -> 注册模型 -> 发送请求(带chat_template_kwargs) -> 验证mock收到的请求 -> 删除模型 -> 停止mock
#
# 验证要点：
#   1. DirectPipeline: chat_template_kwargs 透传到后端 /v1/chat/completions
#   2. TokenPipeline: chat_template_kwargs 被 MessageConverter 消费，不出现在 /v1/completions 中
#   3. TokenPipeline: 请求正常完成（chat_template_kwargs 被正确消费，不导致渲染异常）

# 获取脚本目录并加载utils
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P204: chat_template_kwargs 透传与消费测试（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"

# DirectPipeline 配置
DIRECT_MODEL_NAME="ctk-direct-model"
DIRECT_RUN_ID="run-${SCENARIO_ID}-direct"
DIRECT_SESSION_ID="session-${SCENARIO_ID}-direct-$(date +%s%N | md5sum | head -c 8)"

# TokenPipeline 配置
TITO_MODEL_NAME="ctk-tito-model"
TITO_RUN_ID="run-${SCENARIO_ID}-tito"
TITO_SESSION_ID="session-${SCENARIO_ID}-tito-$(date +%s%N | md5sum | head -c 8)"
TITO_TOKENIZER_PATH="${DEFAULT_TOKENIZER_PATH}"

# Mock服务配置（两个独立端口）
MOCK_PORT_DIRECT=19993
MOCK_PORT_TITO=19994

MOCK_PID=""

# 确保退出时停止mock服务
trap stop_mock EXIT

# ========================================
# 步骤 0: 清理残留模型
# ========================================
log_info "清理可能残留的模型..."
curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${DIRECT_MODEL_NAME}&run_id=${DIRECT_RUN_ID}" > /dev/null 2>&1 || true
curl_with_log -s --noproxy '*' -X DELETE "${TEST_BASE_URL}/models?model_name=${TITO_MODEL_NAME}&run_id=${TITO_RUN_ID}" > /dev/null 2>&1 || true

###############################################################################
# Part A: DirectPipeline — chat_template_kwargs 透传到后端
###############################################################################

echo ""
echo "========================================"
echo "Part A: DirectPipeline - chat_template_kwargs 透传"
echo "========================================"
echo ""

MOCK_PORT=${MOCK_PORT_DIRECT}
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_INFER_URL="http://${MOCK_INFER_HOST}:${MOCK_PORT}/v1"

# 步骤 A1: 启动Mock服务
log_step "步骤 A1: 启动Mock推理服务 (Port: ${MOCK_PORT})"

if ! start_mock; then
    log_error "无法启动Mock服务，测试终止"
    exit 1
fi

echo ""

# 步骤 A2: 注册DirectPipeline模型
log_step "步骤 A2: 注册DirectPipeline模型"

DIRECT_REG_RESP=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${DIRECT_RUN_ID}\",
        \"model_name\": \"${DIRECT_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"token_in_token_out\": false
    }")

DIRECT_REG_BODY=$(echo "$DIRECT_REG_RESP" | sed '$d')
DIRECT_REG_STATUS=$(echo "$DIRECT_REG_RESP" | sed -n '$p')


assert_http_status "200" "$DIRECT_REG_STATUS" "DirectPipeline 注册应返回 200"
assert_eq "success" "$(json_get "$DIRECT_REG_BODY" "status")" "注册模型应返回 success"

sleep 0.5

echo ""

# 步骤 A3: 发送非流式请求（带 chat_template_kwargs）
log_step "步骤 A3: DirectPipeline 非流式请求（带 chat_template_kwargs）"
log_info "chat_template_kwargs: {\"enable_thinking\": true, \"thinking\": true}"

clear_mock_records

DIRECT_RESP=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${DIRECT_RUN_ID}/${DIRECT_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${DIRECT_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"chat_template_kwargs\": {\"enable_thinking\": true, \"thinking\": true},
        \"stream\": false
    }")

DIRECT_BODY=$(echo "$DIRECT_RESP" | sed '$d')
DIRECT_STATUS=$(echo "$DIRECT_RESP" | sed -n '$p')


assert_http_status "200" "$DIRECT_STATUS" "DirectPipeline 请求应返回 200"

echo ""

# 步骤 A4: 验证 chat_template_kwargs 被透传
log_step "步骤 A4: 验证 DirectPipeline - chat_template_kwargs 被透传"

DIRECT_VERIFY=$(verify_infer_request "chat_template_kwargs" "")
log_info "Mock收到的请求验证结果:"
echo "$DIRECT_VERIFY" | grep -E "^(BODY|HEADER):" | while read line; do
    echo "  $line"
done

CTK_VAL=$(echo "$DIRECT_VERIFY" | grep "^BODY:chat_template_kwargs=" | cut -d= -f2)
# 验证 chat_template_kwargs 存在于body中（不为NOT_FOUND即为透传成功）
if [ "$CTK_VAL" != "NOT_FOUND" ]; then
    log_success "DirectPipeline: chat_template_kwargs 已透传到后端"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))

    # 进一步验证值包含 enable_thinking
    assert_contains "$CTK_VAL" "enable_thinking" "透传的 chat_template_kwargs 应包含 enable_thinking"
else
    log_error "DirectPipeline: chat_template_kwargs 未被透传到后端"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# 步骤 A5: 发送流式请求（带 chat_template_kwargs）
log_step "步骤 A5: DirectPipeline 流式请求（带 chat_template_kwargs）"

clear_mock_records

DIRECT_STREAM_RESP=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${DIRECT_RUN_ID}/${DIRECT_SESSION_ID}-stream/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${DIRECT_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hi\"}],
        \"chat_template_kwargs\": {\"enable_thinking\": false},
        \"stream\": true
    }")

assert_contains "$DIRECT_STREAM_RESP" "data:" "流式响应应包含 data: 前缀"
assert_contains "$DIRECT_STREAM_RESP" "[DONE]" "流式响应应以 [DONE] 结束"

echo ""

# 步骤 A6: 验证流式 chat_template_kwargs 被透传
log_step "步骤 A6: 验证 DirectPipeline 流式 - chat_template_kwargs 被透传"

DIRECT_STREAM_VERIFY=$(verify_infer_request "chat_template_kwargs" "")
log_info "流式Mock验证结果:"
echo "$DIRECT_STREAM_VERIFY" | grep -E "^(BODY|HEADER):" | while read line; do
    echo "  $line"
done

CTK_STREAM_VAL=$(echo "$DIRECT_STREAM_VERIFY" | grep "^BODY:chat_template_kwargs=" | cut -d= -f2)
if [ "$CTK_STREAM_VAL" != "NOT_FOUND" ]; then
    log_success "DirectPipeline 流式: chat_template_kwargs 已透传到后端"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "DirectPipeline 流式: chat_template_kwargs 未被透传到后端"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# 步骤 A7: 删除 DirectPipeline 模型
log_step "步骤 A7: 删除 DirectPipeline 模型"
DIRECT_DEL_RESP=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${DIRECT_MODEL_NAME}&run_id=${DIRECT_RUN_ID}")
DIRECT_DEL_BODY=$(echo "$DIRECT_DEL_RESP" | sed '$d')
DIRECT_DEL_STATUS=$(echo "$DIRECT_DEL_RESP" | sed -n '$p')
assert_http_status "200" "$DIRECT_DEL_STATUS" "删除DirectPipeline模型应返回 200"

# 步骤 A8: 停止 DirectPipeline mock
stop_mock
MOCK_PID=""

###############################################################################
# Part B: TokenPipeline — chat_template_kwargs 被消费，不出现在 completion 请求中
###############################################################################

echo ""
echo "========================================"
echo "Part B: TokenPipeline - chat_template_kwargs 消费验证"
echo "========================================"
echo ""

MOCK_PORT=${MOCK_PORT_TITO}
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_INFER_URL="http://${MOCK_INFER_HOST}:${MOCK_PORT}/v1"

# 步骤 B1: 启动Mock服务
log_step "步骤 B1: 启动Mock推理服务 (Port: ${MOCK_PORT})"

if ! start_mock; then
    log_error "无法启动Mock服务，测试终止"
    exit 1
fi

echo ""

# 步骤 B2: 注册TokenPipeline模型
log_step "步骤 B2: 注册TokenPipeline模型"

TITO_REG_RESP=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TITO_RUN_ID}\",
        \"model_name\": \"${TITO_MODEL_NAME}\",
        \"url\": \"${MOCK_INFER_URL}\",
        \"api_key\": \"mock-api-key\",
        \"tokenizer_path\": \"${TITO_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }")

TITO_REG_BODY=$(echo "$TITO_REG_RESP" | sed '$d')
TITO_REG_STATUS=$(echo "$TITO_REG_RESP" | sed -n '$p')


# tokenizer 不可用时跳过 TITO 测试
if [ "$TITO_REG_STATUS" != "200" ]; then
    log_warning "TITO 模型注册失败（可能 tokenizer 不可用），跳过 TokenPipeline 测试"
    log_warning "请确保 ${TITO_TOKENIZER_PATH} tokenizer 可用"
    stop_mock
    MOCK_PID=""
    print_summary
    exit 0
fi

assert_eq "success" "$(json_get "$TITO_REG_BODY" "status")" "注册TITO模型应返回 success"

sleep 1

echo ""

# 步骤 B3: 发送非流式请求（带 chat_template_kwargs）
log_step "步骤 B3: TokenPipeline 非流式请求（带 chat_template_kwargs）"
log_info "chat_template_kwargs: {\"enable_thinking\": true, \"continue_final_message\": false}"

clear_mock_records

TITO_RESP=$(curl_with_log -s --noproxy '*' --max-time 30 -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TITO_RUN_ID}/${TITO_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TITO_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"chat_template_kwargs\": {\"enable_thinking\": true, \"continue_final_message\": false},
        \"stream\": false
    }")

TITO_BODY=$(echo "$TITO_RESP" | sed '$d')
TITO_STATUS=$(echo "$TITO_RESP" | sed -n '$p')


# 关键断言：请求应该成功完成（chat_template_kwargs 被正确消费，不导致异常）
assert_http_status "200" "$TITO_STATUS" "TokenPipeline: 带 chat_template_kwargs 的请求应返回 200"

echo ""

# 步骤 B4: 验证 chat_template_kwargs 不出现在 completion 请求中
log_step "步骤 B4: 验证 TokenPipeline - chat_template_kwargs 不出现在 completion 请求中"

TITO_VERIFY=$(verify_infer_request "chat_template_kwargs" "")
log_info "Mock收到的completion请求验证结果:"
echo "$TITO_VERIFY" | grep -E "^(BODY|HEADER|BODY_JSON):" | while read line; do
    echo "  $line"
done

CTK_TITO_VAL=$(echo "$TITO_VERIFY" | grep "^BODY:chat_template_kwargs=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$CTK_TITO_VAL" "TokenPipeline: chat_template_kwargs 不应出现在 completion 请求中（已被消费）"

echo ""

# 步骤 B5: 发送流式请求（带 chat_template_kwargs）
log_step "步骤 B5: TokenPipeline 流式请求（带 chat_template_kwargs）"

clear_mock_records

TITO_STREAM_RESP=$(curl_with_log -s --noproxy '*' --no-buffer --max-time 15 -X POST "${TEST_BASE_URL}/s/${TITO_RUN_ID}/${TITO_SESSION_ID}-stream/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TITO_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hi\"}],
        \"chat_template_kwargs\": {\"enable_thinking\": false},
        \"stream\": true
    }")

assert_contains "$TITO_STREAM_RESP" "data:" "TokenPipeline 流式响应应包含 data: 前缀"
assert_contains "$TITO_STREAM_RESP" "[DONE]" "TokenPipeline 流式响应应以 [DONE] 结束"

echo ""

# 步骤 B6: 验证流式 chat_template_kwargs 不在 completion 请求中
log_step "步骤 B6: 验证 TokenPipeline 流式 - chat_template_kwargs 不在 completion 请求中"

TITO_STREAM_VERIFY=$(verify_infer_request "chat_template_kwargs" "")
CTK_TITO_STREAM_VAL=$(echo "$TITO_STREAM_VERIFY" | grep "^BODY:chat_template_kwargs=" | cut -d= -f2)
assert_eq "NOT_FOUND" "$CTK_TITO_STREAM_VAL" "TokenPipeline 流式: chat_template_kwargs 不应出现在 completion 请求中"

echo ""

# 步骤 B7: 删除 TokenPipeline 模型
log_step "步骤 B7: 删除 TokenPipeline 模型"
TITO_DEL_RESP=$(curl_with_log -s --noproxy '*' --max-time 10 -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TITO_MODEL_NAME}&run_id=${TITO_RUN_ID}")
TITO_DEL_BODY=$(echo "$TITO_DEL_RESP" | sed '$d')
TITO_DEL_STATUS=$(echo "$TITO_DEL_RESP" | sed -n '$p')
assert_http_status "200" "$TITO_DEL_STATUS" "删除TITO模型应返回 200"

echo ""

# 打印测试摘要
print_summary
