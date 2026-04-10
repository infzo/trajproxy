#!/bin/bash
# 场景 F203: Reasoning场景（Proxy 层）
# 测试流程：注册模型（带reasoning_parser） -> 发送带推理内容的非流式请求 -> 验证响应中reasoning字段非空 -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F203: Reasoning场景（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
REASONING_TEST_BASE_URL="${BASE_URL}"
REASONING_TEST_MODEL_NAME="reasoning-test-model"
REASONING_TEST_RUN_ID="run-${SCENARIO_ID}"
REASONING_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
REASONING_TEST_TOKENIZER_PATH="Qwen/Qwen3.5-2B"
REASONING_TEST_REASONING_PARSER="qwen3"

# 步骤 1: 注册模型（带 run_id、reasoning_parser 和 token_in_token_out）
# 注意：reasoning_parser 只在 token_in_token_out=true 模式下生效
log_step "步骤 1: 注册模型（run_id: ${REASONING_TEST_RUN_ID}, reasoning_parser: ${REASONING_TEST_REASONING_PARSER}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${REASONING_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${REASONING_TEST_RUN_ID}\",
        \"model_name\": \"${REASONING_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${REASONING_TEST_TOKENIZER_PATH}\",
        \"reasoning_parser\": \"${REASONING_TEST_REASONING_PARSER}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${REASONING_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${REASONING_TEST_RUN_ID}\",
        \"model_name\": \"${REASONING_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${REASONING_TEST_TOKENIZER_PATH}\",
        \"reasoning_parser\": \"${REASONING_TEST_REASONING_PARSER}\",
        \"token_in_token_out\": true
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

REGISTER_RUN_ID=$(json_get "$REGISTER_BODY" "run_id")
assert_eq "$REASONING_TEST_RUN_ID" "$REGISTER_RUN_ID" "run_id 应为 ${REASONING_TEST_RUN_ID}"

# 验证 reasoning_parser 配置
REGISTER_REASONING_PARSER=$(json_get "$REGISTER_BODY" "reasoning_parser")
assert_eq "$REASONING_TEST_REASONING_PARSER" "$REGISTER_REASONING_PARSER" "reasoning_parser 应为 ${REASONING_TEST_REASONING_PARSER}"

# 验证 token_in_token_out 配置
REGISTER_TITO=$(json_get_bool "$REGISTER_BODY" "token_in_token_out")
assert_eq "true" "$REGISTER_TITO" "token_in_token_out 应为 true"
sleep 3

echo ""

# 步骤 2: 发送带推理内容的非流式请求
# qwen3 reasoning parser 使用 <think\>...</think\> 标签包裹推理内容
# 使用特定提示词引导模型输出推理格式
log_step "步骤 2: 发送带推理内容的非流式请求（session_id: ${REASONING_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${REASONING_TEST_BASE_URL}/s/${REASONING_TEST_RUN_ID}/${REASONING_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${REASONING_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Output exactly: <think>Let me think about this step by step.\\nFirst, I need to analyze the problem.\\nSecond, I will provide a solution.\\n</think>The answer is 42.\"}],
        \"stream\": false,
        \"max_tokens\": 1024
    }'"
log_separator

CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${REASONING_TEST_BASE_URL}/s/${REASONING_TEST_RUN_ID}/${REASONING_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${REASONING_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Output exactly: <think>Let me think about this step by step.\\nFirst, I need to analyze the problem.\\nSecond, I will provide a solution.\\n</think>The answer is 42.\"}],
        \"stream\": false,
        \"max_tokens\": 1024
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT_STATUS}"
log_response "${CHAT_BODY}"
log_separator

assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 验证响应格式
assert_contains "$CHAT_BODY" "id" "响应应包含 id 字段"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

# 验证 reasoning 字段非空（Reasoning场景关键字段）
if echo "$CHAT_BODY" | grep -q '"reasoning"[[:space:]]*:[[:space:]]*null'; then
    log_error "reasoning 字段为 null，Reasoning场景应有推理内容"
    TEST_FAILED=1
elif echo "$CHAT_BODY" | grep -q '"reasoning"[[:space:]]*:[[:space:]]*""'; then
    log_error "reasoning 字段为空字符串，Reasoning场景应有推理内容"
    TEST_FAILED=1
elif echo "$CHAT_BODY" | grep -q '"reasoning"[[:space:]]*:[[:space:]]*"'; then
    log_success "reasoning 字段非空，检测到推理内容，验证通过"
else
    log_error "响应中无 reasoning 字段，Reasoning场景应有推理内容"
    TEST_FAILED=1
fi

echo ""

# 步骤 3: 查询轨迹确认存在
log_step "步骤 3: 查询轨迹（session_id: ${REASONING_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${REASONING_TEST_BASE_URL}/trajectory?session_id=${REASONING_TEST_SESSION_ID}&limit=100'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${REASONING_TEST_BASE_URL}/trajectory?session_id=${REASONING_TEST_SESSION_ID}&limit=100")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ_STATUS}"
log_response "${TRAJ_BODY}"
log_separator

assert_http_status "200" "$TRAJ_STATUS" "HTTP 状态码应为 200"

# 验证轨迹响应
assert_contains "$TRAJ_BODY" "session_id" "响应应包含 session_id 字段"
assert_contains "$TRAJ_BODY" "count" "响应应包含 count 字段"
assert_contains "$TRAJ_BODY" "records" "响应应包含 records 字段"

# 验证 session_id 匹配
TRAJ_SESSION_ID=$(json_get "$TRAJ_BODY" "session_id")
assert_eq "$REASONING_TEST_SESSION_ID" "$TRAJ_SESSION_ID" "session_id 应为 ${REASONING_TEST_SESSION_ID}"

# 验证记录数大于0
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
if [ "$TRAJ_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "轨迹记录数: ${TRAJ_COUNT}，确认轨迹已捕获"
else
    log_error "轨迹记录数应为大于0，实际为: ${TRAJ_COUNT}"
    TEST_FAILED=1
fi

echo ""

# 步骤 4: 删除模型
log_step "步骤 4: 删除模型（run_id: ${REASONING_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${REASONING_TEST_BASE_URL}/models?model_name=${REASONING_TEST_MODEL_NAME}&run_id=${REASONING_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${REASONING_TEST_BASE_URL}/models?model_name=${REASONING_TEST_MODEL_NAME}&run_id=${REASONING_TEST_RUN_ID}")

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

# 打印测试摘要
print_summary
