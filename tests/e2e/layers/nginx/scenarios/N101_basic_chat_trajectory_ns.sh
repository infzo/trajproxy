#!/bin/bash
# 场景 N101: 基础 chat 轨迹存储（降级冒烟，Nginx 层）
# 来源: 原F100降级为轻量冒烟; 测试流程：注册模型 -> 非流式请求 -> 验证响应 -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 N101: 基础 chat 轨迹存储（降级冒烟，Nginx 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[A-Z][0-9]+' | tr '[:upper:]' '[:lower:]')
CHAT_TEST_BASE_URL="${BASE_URL}"
CHAT_TEST_MODEL_NAME="${DEFAULT_MODEL_NAME}"
CHAT_TEST_RUN_ID="run-${SCENARIO_ID}"
CHAT_TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"

# 步骤 1: 注册模型（带 run_id）
log_step "步骤 1: 注册模型（run_id: ${CHAT_TEST_RUN_ID}）"

REGISTER_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST "${CHAT_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${CHAT_TEST_RUN_ID}\",
        \"model_name\": \"${CHAT_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"token_in_token_out\": false
    }")

REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')


assert_http_status "200" "$REGISTER_STATUS" "HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

REGISTER_RUN_ID=$(json_get "$REGISTER_BODY" "run_id")
assert_eq "$CHAT_TEST_RUN_ID" "$REGISTER_RUN_ID" "run_id 应为 ${CHAT_TEST_RUN_ID}"

sleep 0.3

echo ""

# 步骤 2: 发送非流式推理请求
log_step "步骤 2: 发送非流式推理请求（run_id: ${CHAT_TEST_RUN_ID}, session_id: ${CHAT_TEST_SESSION_ID}）"

CHAT_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST "${CHAT_TEST_BASE_URL}/s/${CHAT_TEST_RUN_ID}/${CHAT_TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CHAT_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "HTTP 状态码应为 200"

# 验证响应格式
assert_contains "$CHAT_BODY" "id" "响应应包含 id 字段"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

echo ""

# 步骤 2.5: 验证轨迹存储（轻量冒烟：查询 trajectory 确认记录存在 + 字段非空）
log_step "步骤 2.5: 验证轨迹存储"
sleep 0.5
TRAJ_RESPONSE=$(curl_with_log -s "${BASE_URL}/trajectory?session_id=${CHAT_TEST_SESSION_ID}&limit=1")
TRAJ_RECORD_COUNT=$(echo "$TRAJ_RESPONSE" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
records = data.get('records', [])
print(len(records))
" 2>/dev/null)

assert_eq "1" "$TRAJ_RECORD_COUNT" "轨迹记录应存在（>=1条）"

TRAJ_FIELDS_OK=$(echo "$TRAJ_RESPONSE" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
records = data.get('records', [])
if not records:
    print('false')
else:
    rec = records[0]
    has_tokens = bool(rec.get('token_ids'))
    has_response = bool(rec.get('response_ids'))
    has_full_conv = bool(rec.get('full_conversation_token_ids'))
    print('true' if has_tokens and has_response and has_full_conv else 'false')
" 2>/dev/null)

assert_eq "true" "$TRAJ_FIELDS_OK" "轨迹字段（token_ids/response_ids/full_conversation_token_ids）应非空"

echo ""

# 步骤 3: 删除模型
log_step "步骤 3: 删除模型（run_id: ${CHAT_TEST_RUN_ID}）"

DELETE_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X DELETE "${CHAT_TEST_BASE_URL}/models?model_name=${CHAT_TEST_MODEL_NAME}&run_id=${CHAT_TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')


assert_http_status "200" "$DELETE_STATUS" "HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

DELETE_DELETED=$(json_get_bool "$DELETE_BODY" "deleted")
assert_eq "true" "$DELETE_DELETED" "deleted 应为 true"

echo ""

# 打印测试摘要
print_summary