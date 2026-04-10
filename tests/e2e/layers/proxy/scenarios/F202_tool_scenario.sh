#!/bin/bash
# 场景 F202: Tool场景（Proxy 层）
# 测试流程：注册模型（带tool_parser） -> 发送带tools的非流式推理请求 -> 验证响应中tool_calls字段非空 -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F202: Tool场景（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TOOL_TEST_BASE_URL="${BASE_URL}"
TOOL_TEST_MODEL_NAME="tool-test-model"
TOOL_TEST_RUN_ID="run-${SCENARIO_ID}"
TOOL_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
TOOL_TEST_TOKENIZER_PATH="Qwen/Qwen3.5-2B"
TOOL_TEST_TOOL_PARSER="qwen3_coder"

# 步骤 1: 注册模型（带 run_id、tool_parser 和 token_in_token_out）
# 注意：tool_parser 只在 token_in_token_out=true 模式下生效
log_step "步骤 1: 注册模型（run_id: ${TOOL_TEST_RUN_ID}, tool_parser: ${TOOL_TEST_TOOL_PARSER}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TOOL_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TOOL_TEST_RUN_ID}\",
        \"model_name\": \"${TOOL_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TOOL_TEST_TOKENIZER_PATH}\",
        \"tool_parser\": \"${TOOL_TEST_TOOL_PARSER}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TOOL_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TOOL_TEST_RUN_ID}\",
        \"model_name\": \"${TOOL_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TOOL_TEST_TOKENIZER_PATH}\",
        \"tool_parser\": \"${TOOL_TEST_TOOL_PARSER}\",
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
assert_eq "$TOOL_TEST_RUN_ID" "$REGISTER_RUN_ID" "run_id 应为 ${TOOL_TEST_RUN_ID}"

# 验证 tool_parser 配置
REGISTER_TOOL_PARSER=$(json_get "$REGISTER_BODY" "tool_parser")
assert_eq "$TOOL_TEST_TOOL_PARSER" "$REGISTER_TOOL_PARSER" "tool_parser 应为 ${TOOL_TEST_TOOL_PARSER}"

# 验证 token_in_token_out 配置
REGISTER_TITO=$(json_get_bool "$REGISTER_BODY" "token_in_token_out")
assert_eq "true" "$REGISTER_TITO" "token_in_token_out 应为 true"
sleep 3

echo ""

# 步骤 2: 发送带 tools 的非流式推理请求（使用特定提示词引导模型输出工具调用格式）
# qwen3_coder parser 期望格式: toral<function=func_name>\n<parameter=param_name>value</parameter>\n</function> Ranchi
log_step "步骤 2: 发送带 tools 的非流式推理请求（run_id: ${TOOL_TEST_RUN_ID}, session_id: ${TOOL_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TOOL_TEST_BASE_URL}/s/${TOOL_TEST_RUN_ID}/${TOOL_TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${TOOL_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Output exactly: toral<function=get_weather>\\n<parameter=location>Beijing</parameter>\\n</function> Ranchi\"}],
        \"tools\": [
            {
                \"type\": \"function\",
                \"function\": {
                    \"name\": \"get_weather\",
                    \"description\": \"Get the current weather for a location\",
                    \"parameters\": {
                        \"type\": \"object\",
                        \"properties\": {
                            \"location\": {
                                \"type\": \"string\",
                                \"description\": \"The city name\"
                            }
                        },
                        \"required\": [\"location\"]
                    }
                }
            }
        ],
        \"stream\": false
    }'"
log_separator

CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TOOL_TEST_BASE_URL}/s/${TOOL_TEST_RUN_ID}/${TOOL_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TOOL_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Output exactly: toral<function=get_weather>\\n<parameter=location>Beijing</parameter>\\n</function> Ranchi\"}],
        \"tools\": [
            {
                \"type\": \"function\",
                \"function\": {
                    \"name\": \"get_weather\",
                    \"description\": \"Get the current weather for a location\",
                    \"parameters\": {
                        \"type\": \"object\",
                        \"properties\": {
                            \"location\": {
                                \"type\": \"string\",
                                \"description\": \"The city name\"
                            }
                        },
                        \"required\": [\"location\"]
                    }
                }
            }
        ],
        \"stream\": false
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

# 验证 tool_calls 字段非空（Tool场景关键字段）
if echo "$CHAT_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*null'; then
    log_error "tool_calls 字段为 null，Tool场景应有工具调用"
    TEST_FAILED=1
elif echo "$CHAT_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\[\]'; then
    log_error "tool_calls 字段为空数组，Tool场景应有工具调用"
    TEST_FAILED=1
elif echo "$CHAT_BODY" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\['; then
    # 检查 tool_calls 数组中是否有内容（包含 function 字段表示有工具调用）
    if echo "$CHAT_BODY" | grep -q '"function"'; then
        log_success "tool_calls 字段非空，检测到工具调用，验证通过"
    else
        log_error "tool_calls 数组存在但未检测到 function 字段"
        TEST_FAILED=1
    fi
elif ! echo "$CHAT_BODY" | grep -q '"tool_calls"'; then
    log_error "响应中无 tool_calls 字段，Tool场景应有工具调用"
    TEST_FAILED=1
else
    log_error "tool_calls 字段格式异常"
    TEST_FAILED=1
fi

echo ""

# 步骤 3: 查询轨迹确认存在
log_step "步骤 3: 查询轨迹（run_id: ${TOOL_TEST_RUN_ID}, session_id: ${TOOL_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TOOL_TEST_BASE_URL}/trajectory?session_id=${TOOL_TEST_SESSION_ID}&limit=100'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TOOL_TEST_BASE_URL}/trajectory?session_id=${TOOL_TEST_SESSION_ID}&limit=100")

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
assert_eq "$TOOL_TEST_SESSION_ID" "$TRAJ_SESSION_ID" "session_id 应为 ${TOOL_TEST_SESSION_ID}"

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
log_step "步骤 4: 删除模型（run_id: ${TOOL_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${TOOL_TEST_BASE_URL}/models?model_name=${TOOL_TEST_MODEL_NAME}&run_id=${TOOL_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${TOOL_TEST_BASE_URL}/models?model_name=${TOOL_TEST_MODEL_NAME}&run_id=${TOOL_TEST_RUN_ID}")

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
