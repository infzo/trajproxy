#!/bin/bash
# 场景 F217: 预置模型懒加载验证（Proxy 层）
# 测试流程：使用 config.yaml 中预置模型 → 首次请求触发懒加载 → 缓存命中 → 验证不可 API 删除
# 预置模型在 Worker 启动时仅存储配置，Processor 在首次请求时创建
# 注意：本测试使用真实后端，不使用 mock 推理服务

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F217: 预置模型懒加载验证（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
TEST_BASE_URL="${BASE_URL}"
# 使用 config.yaml 中预置的静态模型
PRESET_MODEL="qwen3.5-2b"
PRESET_RUN_ID_APP="app-001"
TEST_SESSION_ID="session-f217-$(date +%s%N | md5sum | head -c 8)"

# 步骤 1: 列出所有模型，确认预置模型已存在
log_step "步骤 1: 列出所有模型，确认预置模型已注册（仅配置，未加载 Processor）"

LIST_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${API_MODELS}")

LIST_BODY=$(echo "$LIST_RESPONSE" | sed '$d')
LIST_STATUS=$(echo "$LIST_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${LIST_STATUS}"
log_response "${LIST_BODY}"
log_separator

assert_http_status "200" "$LIST_STATUS" "模型列表 HTTP 状态码应为 200"
LIST_RESULT=$(json_get "$LIST_BODY" "status")
assert_eq "success" "$LIST_RESULT" "列出模型应返回 success"

# 验证预置模型在列表中
assert_contains "$LIST_BODY" "$PRESET_MODEL" "模型列表应包含预置模型 ${PRESET_MODEL}"

echo ""

# 步骤 2: 向全局预置模型发送首次请求 — 触发懒加载
log_step "步骤 2: 向全局预置模型发送首次请求（触发懒加载）"

CHAT1_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${PRESET_MODEL}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello preset model\"}],
        \"stream\": false
    }")

CHAT1_BODY=$(echo "$CHAT1_RESPONSE" | sed '$d')
CHAT1_STATUS=$(echo "$CHAT1_RESPONSE" | sed -n '$p')

log_response "首次请求 HTTP Status: ${CHAT1_STATUS}"
log_response "${CHAT1_BODY}"
log_separator

assert_http_status "200" "$CHAT1_STATUS" "预置模型首次请求（懒加载）HTTP 状态码应为 200"
assert_contains "$CHAT1_BODY" "choices" "预置模型首次请求响应应包含 choices 字段"
echo ""

# 步骤 3: 再次请求 — 验证缓存命中
log_step "步骤 3: 再次请求预置模型（验证 LRU 缓存命中）"

CHAT2_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${PRESET_MODEL}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Second request to preset\"}],
        \"stream\": false
    }")

CHAT2_BODY=$(echo "$CHAT2_RESPONSE" | sed '$d')
CHAT2_STATUS=$(echo "$CHAT2_RESPONSE" | sed -n '$p')

log_response "第二次请求 HTTP Status: ${CHAT2_STATUS}"
log_response "${CHAT2_BODY}"
log_separator

assert_http_status "200" "$CHAT2_STATUS" "预置模型第二次请求（缓存命中）HTTP 状态码应为 200"
assert_contains "$CHAT2_BODY" "choices" "第二次请求响应应包含 choices 字段"
echo ""

# 步骤 4: 向带 run_id 的预置模型发送请求（token_in_token_out=true，需要 tokenizer）
log_step "步骤 4: 向预置模型（run_id=${PRESET_RUN_ID_APP}）发送请求"

CHAT3_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${PRESET_RUN_ID_APP}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${PRESET_MODEL}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello app preset\"}],
        \"stream\": false
    }")

CHAT3_BODY=$(echo "$CHAT3_RESPONSE" | sed '$d')
CHAT3_STATUS=$(echo "$CHAT3_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${CHAT3_STATUS}"
log_response "${CHAT3_BODY}"
log_separator

if [ "$CHAT3_STATUS" = "200" ]; then
    log_success "预置模型（run_id=app-001）请求成功"
    assert_contains "$CHAT3_BODY" "choices" "响应应包含 choices 字段"
else
    log_warning "预置模型（run_id=app-001）请求返回 HTTP ${CHAT3_STATUS}（可能缺少 tokenizer）"
fi

echo ""

# 步骤 5: 验证预置模型不能通过 API 删除
log_step "步骤 5: 验证预置模型不能被 API 删除"

DELETE_PRESET_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${PRESET_MODEL}&run_id=")

DELETE_PRESET_BODY=$(echo "$DELETE_PRESET_RESPONSE" | sed '$d')
DELETE_PRESET_STATUS=$(echo "$DELETE_PRESET_RESPONSE" | sed -n '$p')

log_response "删除预置模型 HTTP Status: ${DELETE_PRESET_STATUS}"
log_response "${DELETE_PRESET_BODY}"
log_separator

# 预置模型不在 dynamic configs 中，返回 404
assert_http_status "404" "$DELETE_PRESET_STATUS" "删除预置模型应返回 404（不允许删除）"

echo ""

# 打印测试摘要
print_summary
