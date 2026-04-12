#!/bin/bash
# 场景 F111: Claude格式 Reasoning + Tool 组合流式场景（Nginx 层）
# 测试流程：注册模型（带reasoning_parser和tool_parser） -> 发送Claude格式带推理内容和工具调用的流式请求 -> 验证流式响应中reasoning和tool_calls解析 -> 删除模型
# 特点：使用 /v1/messages 端点（Claude API格式）+ 流式响应

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F111: Claude格式 Reasoning + Tool 组合流式场景（Nginx 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
CLAUDE_COMBO_STREAM_TEST_BASE_URL="${BASE_URL}"
CLAUDE_COMBO_STREAM_TEST_MODEL_NAME="claude-reasoning-tool-stream-test-model"
CLAUDE_COMBO_STREAM_TEST_RUN_ID="run-${SCENARIO_ID}"
CLAUDE_COMBO_STREAM_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
CLAUDE_COMBO_STREAM_TEST_TOKENIZER_PATH="Qwen/Qwen3.5-2B"
CLAUDE_COMBO_STREAM_TEST_TOOL_PARSER="qwen3_coder"
CLAUDE_COMBO_STREAM_TEST_REASONING_PARSER="qwen3"

# 步骤 1: 注册模型（同时配置 reasoning_parser 和 tool_parser）
# 注意：parser 只在 token_in_token_out=true 模式下生效
log_step "步骤 1: 注册模型（run_id: ${CLAUDE_COMBO_STREAM_TEST_RUN_ID}, reasoning_parser: ${CLAUDE_COMBO_STREAM_TEST_REASONING_PARSER}, tool_parser: ${CLAUDE_COMBO_STREAM_TEST_TOOL_PARSER}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${CLAUDE_COMBO_STREAM_TEST_RUN_ID}\",
        \"model_name\": \"${CLAUDE_COMBO_STREAM_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${CLAUDE_COMBO_STREAM_TEST_TOKENIZER_PATH}\",
        \"reasoning_parser\": \"${CLAUDE_COMBO_STREAM_TEST_REASONING_PARSER}\",
        \"tool_parser\": \"${CLAUDE_COMBO_STREAM_TEST_TOOL_PARSER}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${CLAUDE_COMBO_STREAM_TEST_RUN_ID}\",
        \"model_name\": \"${CLAUDE_COMBO_STREAM_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${CLAUDE_COMBO_STREAM_TEST_TOKENIZER_PATH}\",
        \"reasoning_parser\": \"${CLAUDE_COMBO_STREAM_TEST_REASONING_PARSER}\",
        \"tool_parser\": \"${CLAUDE_COMBO_STREAM_TEST_TOOL_PARSER}\",
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
assert_eq "$CLAUDE_COMBO_STREAM_TEST_RUN_ID" "$REGISTER_RUN_ID" "run_id 应为 ${CLAUDE_COMBO_STREAM_TEST_RUN_ID}"

# 验证 reasoning_parser 配置
REGISTER_REASONING_PARSER=$(json_get "$REGISTER_BODY" "reasoning_parser")
assert_eq "$CLAUDE_COMBO_STREAM_TEST_REASONING_PARSER" "$REGISTER_REASONING_PARSER" "reasoning_parser 应为 ${CLAUDE_COMBO_STREAM_TEST_REASONING_PARSER}"

# 验证 tool_parser 配置
REGISTER_TOOL_PARSER=$(json_get "$REGISTER_BODY" "tool_parser")
assert_eq "$CLAUDE_COMBO_STREAM_TEST_TOOL_PARSER" "$REGISTER_TOOL_PARSER" "tool_parser 应为 ${CLAUDE_COMBO_STREAM_TEST_TOOL_PARSER}"

# 验证 token_in_token_out 配置
REGISTER_TITO=$(json_get_bool "$REGISTER_BODY" "token_in_token_out")
assert_eq "true" "$REGISTER_TITO" "token_in_token_out 应为 true"
sleep 3

echo ""

# 步骤 2: 发送Claude格式带推理内容和工具调用的流式请求
# Claude API tools 格式与 OpenAI 不同，使用 tool_choice 和 tools 数组
# qwen3 reasoning parser 使用 标签包裹推理内容
# qwen3_coder tool parser 期望格式: toral<function=func_name>\n<parameter=param_name>value</parameter>\n</function> Ranchi
log_step "步骤 2: 发送Claude格式带推理内容和工具调用的流式请求（session_id: ${CLAUDE_COMBO_STREAM_TEST_SESSION_ID}）"
log_curl_cmd "curl -s --no-buffer \\
    -X POST '${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/s/${CLAUDE_COMBO_STREAM_TEST_RUN_ID}/${CLAUDE_COMBO_STREAM_TEST_SESSION_ID}/v1/messages' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${CLAUDE_COMBO_STREAM_TEST_MODEL_NAME}\",
        \"max_tokens\": 1024,
        \"messages\": [{\"role\": \"user\", \"content\": \"Output exactly: Let me think about the weather.\\nFirst, I will check the location.\\n\\n\\nNow I will call the weather function.\\n\\ntoral<function=get_weather>\\n<parameter=location>Beijing</parameter>\\n</function> Ranchi\"}],
        \"tools\": [
            {
                \"name\": \"get_weather\",
                \"description\": \"Get the current weather for a location\",
                \"input_schema\": {
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
        ],
        \"stream\": true
    }'"
log_separator

# 发送流式请求并收集完整响应
STREAM_RESPONSE=$(curl -s --no-buffer -X POST "${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/s/${CLAUDE_COMBO_STREAM_TEST_RUN_ID}/${CLAUDE_COMBO_STREAM_TEST_SESSION_ID}/v1/messages" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CLAUDE_COMBO_STREAM_TEST_MODEL_NAME}\",
        \"max_tokens\": 1024,
        \"messages\": [{\"role\": \"user\", \"content\": \"Output exactly: Let me think about the weather.\\nFirst, I will check the location.\\n\\n\\nNow I will call the weather function.\\n\\ntoral<function=get_weather>\\n<parameter=location>Beijing</parameter>\\n</function> Ranchi\"}],
        \"tools\": [
            {
                \"name\": \"get_weather\",
                \"description\": \"Get the current weather for a location\",
                \"input_schema\": {
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
        ],
        \"stream\": true
    }")

log_response "流式响应内容:"
echo "$STREAM_RESPONSE"
log_separator

# 验证流式响应格式（Claude API流式响应格式）
assert_contains "$STREAM_RESPONSE" "event:" "Claude流式响应应包含 event: 前缀"
assert_contains "$STREAM_RESPONSE" "data:" "Claude流式响应应包含 data: 字段"

# 统计事件数量
EVENT_COUNT=$(echo "$STREAM_RESPONSE" | grep -c "^event:" || true)
log_info "收到 ${EVENT_COUNT} 个事件"

if [ "$EVENT_COUNT" -gt 0 ]; then
    log_success "Claude流式响应包含多个事件"
else
    log_error "Claude流式响应未包含有效事件"
fi

# 验证流式响应中 reasoning 字段（Reasoning 流式场景关键字段）
# Claude流式响应中，reasoning 可能通过 content_block_delta 中的 reasoning 字段返回
REASONING_DETECTED=false
if echo "$STREAM_RESPONSE" | grep -q '"reasoning_content"'; then
    log_success "流式响应中检测到 reasoning_content 解析"
    REASONING_DETECTED=true
elif echo "$STREAM_RESPONSE" | grep -q '"reasoning"[[:space:]]*:[[:space:]]*"'; then
    log_success "流式响应中检测到 reasoning 解析"
    REASONING_DETECTED=true
else
    log_error "流式响应中未检测到 reasoning 相关字段，组合场景应有推理内容解析"
    TEST_FAILED=1
fi

# 验证流式响应中 tool 相关字段（Tool 流式场景关键字段）
# Claude流式响应中，tool_use 可能在 content_block_start 或 content_block_delta 中出现
TOOL_DETECTED=false
if echo "$STREAM_RESPONSE" | grep -q '"tool_use"'; then
    log_success "流式响应中检测到 tool_use 字段（Claude格式）"
    TOOL_DETECTED=true
elif echo "$STREAM_RESPONSE" | grep -q '"tool_calls"[[:space:]]*:[[:space:]]*\['; then
    if echo "$STREAM_RESPONSE" | grep -q '"function"'; then
        log_success "流式响应中检测到 tool_calls 解析"
        TOOL_DETECTED=true
    else
        log_error "tool_calls 数组存在但未检测到 function 字段"
        TEST_FAILED=1
    fi
elif echo "$STREAM_RESPONSE" | grep -q '"name"[[:space:]]*:[[:space:]]*"get_weather"'; then
    log_success "流式响应中检测到工具调用内容（get_weather）"
    TOOL_DETECTED=true
else
    log_error "流式响应中未检测到工具调用相关字段"
    TEST_FAILED=1
fi

# 组合场景：同时检测到 reasoning 和 tool
if [ "$REASONING_DETECTED" = true ] && [ "$TOOL_DETECTED" = true ]; then
    log_success "Claude格式 Reasoning + Tool 组合流式场景验证通过"
fi

echo ""

# 步骤 3: 查询轨迹确认存在
log_step "步骤 3: 查询轨迹（session_id: ${CLAUDE_COMBO_STREAM_TEST_SESSION_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/trajectory?session_id=${CLAUDE_COMBO_STREAM_TEST_SESSION_ID}&limit=100'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/trajectory?session_id=${CLAUDE_COMBO_STREAM_TEST_SESSION_ID}&limit=100")

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
assert_eq "$CLAUDE_COMBO_STREAM_TEST_SESSION_ID" "$TRAJ_SESSION_ID" "session_id 应为 ${CLAUDE_COMBO_STREAM_TEST_SESSION_ID}"

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
log_step "步骤 4: 删除模型（run_id: ${CLAUDE_COMBO_STREAM_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/models?model_name=${CLAUDE_COMBO_STREAM_TEST_MODEL_NAME}&run_id=${CLAUDE_COMBO_STREAM_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${CLAUDE_COMBO_STREAM_TEST_BASE_URL}/models?model_name=${CLAUDE_COMBO_STREAM_TEST_MODEL_NAME}&run_id=${CLAUDE_COMBO_STREAM_TEST_RUN_ID}")

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