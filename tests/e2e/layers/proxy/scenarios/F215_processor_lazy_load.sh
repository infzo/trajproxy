#!/bin/bash
# 场景 F215: Processor 懒加载验证（Proxy 层）
# 测试流程：注册模型 → 首次请求懒加载 → 缓存命中 → 清理
# 注意：使用真实后端，不依赖 mock 推理服务

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F215: Processor 懒加载验证（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="test-lazy-model"
TEST_RUN_ID="run-lazy-001"
TEST_SESSION_ID="session-f215-$(date +%s%N | md5sum | head -c 8)"

# 步骤 1: 动态注册模型（仅存储配置，不加载 Processor）
log_step "步骤 1: 动态注册模型（仅存储配置，不加载 Processor）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${API_MODELS}/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${API_MODELS}/register" \
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

assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"
REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 1
echo ""

# 步骤 2: 首次请求 — 触发懒加载
log_step "步骤 2: 首次请求（触发 Processor 懒加载，tokenizer 等资源此时才创建）"

CHAT1_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello, lazy load test\"}],
        \"stream\": false
    }")

CHAT1_BODY=$(echo "$CHAT1_RESPONSE" | sed '$d')
CHAT1_STATUS=$(echo "$CHAT1_RESPONSE" | sed -n '$p')

log_response "首次请求 HTTP Status: ${CHAT1_STATUS}"
log_response "${CHAT1_BODY}"
log_separator

assert_http_status "200" "$CHAT1_STATUS" "首次请求（懒加载）HTTP 状态码应为 200"
assert_contains "$CHAT1_BODY" "choices" "首次请求响应应包含 choices 字段"
echo ""

# 步骤 3: 再次请求 — 验证缓存命中
log_step "步骤 3: 再次请求同一模型（应命中 LRU 缓存，不重新加载）"

CHAT2_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-2/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Second request\"}],
        \"stream\": false
    }")

CHAT2_BODY=$(echo "$CHAT2_RESPONSE" | sed '$d')
CHAT2_STATUS=$(echo "$CHAT2_RESPONSE" | sed -n '$p')

log_response "第二次请求 HTTP Status: ${CHAT2_STATUS}"
log_response "${CHAT2_BODY}"
log_separator

assert_http_status "200" "$CHAT2_STATUS" "第二次请求（缓存命中）HTTP 状态码应为 200"
assert_contains "$CHAT2_BODY" "choices" "第二次请求响应应包含 choices 字段"
echo ""

# 步骤 4: 流式请求 — 验证缓存 Processor 支持流式
log_step "步骤 4: 流式请求同一模型（验证缓存 Processor 支持流式）"

STREAM_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}-3/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Stream test\"}],
        \"stream\": true
    }")

STREAM_BODY=$(echo "$STREAM_RESPONSE" | sed '$d')
STREAM_STATUS=$(echo "$STREAM_RESPONSE" | sed -n '$p')

log_response "流式请求 HTTP Status: ${STREAM_STATUS}"
log_separator

assert_http_status "200" "$STREAM_STATUS" "流式请求 HTTP 状态码应为 200"
assert_contains "$STREAM_BODY" "data:" "流式响应应包含 SSE data 前缀"
echo ""

# 步骤 5: 清理 — 删除模型（同时淘汰缓存中的 Processor）
log_step "步骤 5: 删除模型"

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"
DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

# 打印测试摘要
print_summary
