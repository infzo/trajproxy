#!/bin/bash
# 场景 S101: 精简存储模式验证
# S 系列：存储模式专项测试，需要服务以 storage_mode=compact 部署
# 前置条件: 服务以 storage_mode=compact 部署（否则本用例会失败）
# 运行方式: run_tests.sh S101 或 run_tests.sh --all（显式指定 S 前缀）
# 测试流程：注册TITO模型 -> 非流式请求 -> 流式请求 -> 查询轨迹验证字段状态 -> 删除模型

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 S101: 精简存储模式验证"
echo "========================================"
echo ""

# ---- 测试配置 ----
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
TEST_TOKENIZER_PATH="${DEFAULT_TOKENIZER_PATH}"

# ========== 步骤 1: 注册 TITO 模型 ==========
log_step "步骤 1: 注册 TITO 模型（run_id: ${TEST_RUN_ID}, token_in_token_out: true）"

REGISTER_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true,
        \"tool_parser\": \"${DEFAULT_TOOL_PARSER}\",
        \"reasoning_parser\": \"${DEFAULT_REASONING_PARSER}\"
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')


assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 0.5

echo ""

# ========== 步骤 2: 发送非流式推理请求 ==========
log_step "步骤 2: 发送非流式推理请求（run_id: ${TEST_RUN_ID}, session_id: ${TEST_SESSION_ID}）"

CHAT_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST \
    "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [
            {\"role\": \"system\", \"content\": \"你是一个有帮助的AI助手\"},
            {\"role\": \"user\", \"content\": \"你好，请简单介绍一下你自己\"}
        ],
        \"max_tokens\": 128,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "非流式请求 HTTP 状态码应为 200"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

echo ""

# ========== 步骤 3: 发送流式推理请求 ==========
log_step "步骤 3: 发送流式推理请求"

STREAM_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST \
    "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [
            {\"role\": \"user\", \"content\": \"请告诉我1+1等于几\"}
        ],
        \"max_tokens\": 64,
        \"stream\": true
    }")

STREAM_BODY=$(echo "$STREAM_RESPONSE" | sed '$d')
STREAM_STATUS=$(echo "$STREAM_RESPONSE" | sed -n '$p')


assert_http_status "200" "$STREAM_STATUS" "流式请求 HTTP 状态码应为 200"

echo ""

sleep 1  # 等待轨迹存储完成

# ========== 步骤 4: 查询轨迹并验证精简模式字段状态 ==========
log_step "步骤 4: 验证精简模式字段状态"

TRAJ_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" \
    -X GET "${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=10")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')


assert_http_status "200" "$TRAJ_STATUS" "查询轨迹 HTTP 状态码应为 200"

TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
assert_eq "2" "$TRAJ_COUNT" "轨迹记录数应为 2（非流式 + 流式）"

# ---- 核心验证：精简模式下 6 个字段应为 null ----
COMPACT_CHECK_RESULT=$(echo "$TRAJ_BODY" | python3 -c "
import sys, json

data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) < 2:
    print('FAIL:轨迹记录数不足2')
    sys.exit(0)

errors = []

# 精简模式下应为 null 的字段
compact_null_fields = ['messages', 'text_request', 'text_response', 'token_ids', 'response_ids', 'token_request']

# 精简模式下应正常存在的字段
compact_present_fields = ['raw_request', 'raw_response', 'prompt_text', 'response_text',
                          'full_conversation_text', 'full_conversation_token_ids', 'tokenizer_path']

for i, record in enumerate(records):
    record_type = '非流式' if i == 0 else '流式'

    # 验证冗余字段为 null/None
    for field in compact_null_fields:
        val = record.get(field)
        if val is not None and val != []:
            errors.append(f'{record_type}记录: {field} 应为 null，实际: {repr(val)[:50]}')

    # 验证核心字段不为 null
    for field in compact_present_fields:
        val = record.get(field)
        if val is None or val == '' or val == []:
            errors.append(f'{record_type}记录: {field} 不应为空')

    # 验证 raw_request 中包含 messages（可以从 raw_request 获取 messages）
    raw_req = record.get('raw_request')
    if raw_req and isinstance(raw_req, dict):
        msgs = raw_req.get('messages')
        if not msgs or not isinstance(msgs, list):
            errors.append(f'{record_type}记录: raw_request.messages 为空或格式异常')
    else:
        errors.append(f'{record_type}记录: raw_request 为空或格式异常')

    # 验证元数据字段正常
    meta_fields = ['unique_id', 'session_id', 'run_id', 'model', 'prompt_tokens', 'completion_tokens',
                   'total_tokens', 'processing_duration_ms']
    for field in meta_fields:
        val = record.get(field)
        if val is None:
            errors.append(f'{record_type}记录: 元数据字段 {field} 为 null')

    # 验证 full_conversation_token_ids 长度一致性
    full_conv = record.get('full_conversation_token_ids')
    prompt_tk = record.get('prompt_tokens')
    completion_tk = record.get('completion_tokens')
    if full_conv and isinstance(full_conv, list) and prompt_tk is not None and completion_tk is not None:
        expected_len = prompt_tk + completion_tk
        actual_len = len(full_conv)
        if actual_len != expected_len:
            errors.append(f'{record_type}记录: full_conversation_token_ids 长度({actual_len}) != prompt_tokens({prompt_tk}) + completion_tokens({completion_tk})')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print('PASS:精简模式字段状态正确（6个冗余字段为null，核心字段完整）')
" 2>/dev/null)

if echo "$COMPACT_CHECK_RESULT" | grep -q "^PASS:"; then
    log_success "$COMPACT_CHECK_RESULT"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "$COMPACT_CHECK_RESULT"
fi

echo ""

# ========== 步骤 5: 删除模型 ==========
log_step "步骤 5: 删除模型（run_id: ${TEST_RUN_ID}）"

DELETE_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" \
    -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')


assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

print_summary
