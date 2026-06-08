#!/bin/bash
# 场景 P301: 轨迹捕获基础（Proxy 层）
# 来源: 原F103, 保留; 测试流程：注册TITO模型 -> 非流式请求 -> 查询轨迹确认记录存在 -> 删除模型

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 P301: 轨迹捕获基础"
echo "========================================"
echo ""

# 测试配置
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
        \"token_in_token_out\": true
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

CHAT_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"你好，请介绍一下你自己\"}],
        \"max_tokens\": 128,
        \"stream\": false
    }")

CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')


assert_http_status "200" "$CHAT_STATUS" "非流式请求 HTTP 状态码应为 200"
assert_contains "$CHAT_BODY" "choices" "响应应包含 choices 字段"

echo ""

# ========== 步骤 3: 查询轨迹，确认记录存在 ==========

log_step "步骤 3: 查询轨迹（session_id: ${TEST_SESSION_ID}）"

TRAJ_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=10")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')


assert_http_status "200" "$TRAJ_STATUS" "查询轨迹 HTTP 状态码应为 200"

# 验证轨迹记录数
TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
assert_eq "1" "$TRAJ_COUNT" "轨迹记录数应为 1"

# 验证轨迹字段存在且非空
TRAJ_CHECK_RESULT=$(echo "$TRAJ_BODY" | python3 -c "
import sys, json

data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) < 1:
    print('FAIL:轨迹记录数为0')
    sys.exit(0)

record = records[0]
errors = []

# 验证核心字段存在且非空
required_fields = ['run_id', 'session_id', 'model_name']
for field in required_fields:
    val = record.get(field, '')
    if not val:
        errors.append(f'{field} 为空或缺失')

# 验证 messages 字段存在且非空
messages = record.get('messages', [])
if not messages:
    errors.append('messages 为空或缺失')

# 验证 token_ids 相关字段存在（TITO 模式应有）
token_ids_fields = ['response_ids', 'full_conversation_token_ids']
for field in token_ids_fields:
    val = record.get(field, [])
    if not val:
        errors.append(f'{field} 为空或缺失')

# 验证 cache_hit_tokens == 0（第1轮，无缓存命中）
cache_hit = record.get('cache_hit_tokens', -1)
if cache_hit != 0:
    errors.append(f'cache_hit_tokens 应为 0, 实际: {cache_hit}')

# 验证 run_id 和 session_id 与请求一致
if record.get('run_id', '') != '${TEST_RUN_ID}':
    errors.append(f'run_id 不匹配: 期望 ${TEST_RUN_ID}, 实际 {record.get(\"run_id\", \"\")}')

if record.get('session_id', '') != '${TEST_SESSION_ID}':
    errors.append(f'session_id 不匹配: 期望 ${TEST_SESSION_ID}, 实际 {record.get(\"session_id\", \"\")}')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print(f'PASS:轨迹记录存在, run_id=${TEST_RUN_ID}, session_id=${TEST_SESSION_ID}, cache_hit_tokens=0')
" 2>/dev/null)

if echo "$TRAJ_CHECK_RESULT" | grep -q "^PASS:"; then
    log_success "$TRAJ_CHECK_RESULT"
else
    log_error "$TRAJ_CHECK_RESULT"
fi

echo ""

# ========== 步骤 4: 删除模型 ==========

log_step "步骤 4: 删除模型（run_id: ${TEST_RUN_ID}）"

DELETE_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')


assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

print_summary