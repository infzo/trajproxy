#!/bin/bash
# P309: 轨迹字段与请求内容交叉验证（新增）
# 矩阵: TITO×ns×单轮×交叉校验
# 来源: 新增; 验证存储的messages/token_ids与实际请求内容一致

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P309: 轨迹字段交叉验证 ==="

RUN_ID="run-p309"
SESS_ID="sess-f211-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"
TEST_PROMPT="Hello crosscheck test 211"

log_step "注册 TITO 模型"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\"}" > /dev/null
sleep 1

log_step "发送已知内容请求"
R1=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"${TEST_PROMPT}\"}],\"max_tokens\":128}")
R1_BODY=$(echo "$R1" | sed '$d')
R1_STATUS=$(echo "$R1" | sed -n '$p')
assert_http_status "200" "$R1_STATUS" "请求 200"
sleep 0.5

log_step "查询轨迹并交叉验证"
TRAJ=$(curl_with_log -s "${BASE_URL}/trajectory?session_id=${SESS_ID}&limit=10")

CROSSCHECK=$(echo "$TRAJ" | python3 -c "
import json, sys

data = json.loads(sys.stdin.read())
records = data.get('records', [])
if not records:
    print('FAIL:无轨迹记录')
    sys.exit(0)

rec = records[0]
errors = []

# 1. 验证存储的messages包含发送的content
messages = rec.get('messages', [])
found_prompt = False
for msg in messages:
    if msg.get('role') == 'user' and '${TEST_PROMPT}' in msg.get('content', ''):
        found_prompt = True
        break
if not found_prompt:
    errors.append('messages中未找到发送的prompt内容')

# 2. 验证token_ids非空
token_ids = rec.get('token_ids', [])
if not token_ids:
    errors.append('token_ids为空')

# 3. 验证response_ids非空
response_ids = rec.get('response_ids', [])
if not response_ids:
    errors.append('response_ids为空')

# 4. 校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)
full_conv_ids = rec.get('full_conversation_token_ids', [])
if full_conv_ids and token_ids and response_ids:
    if len(full_conv_ids) != len(token_ids) + len(response_ids):
        errors.append(f'校验2: full_conv={len(full_conv_ids)} != token_ids={len(token_ids)}+response_ids={len(response_ids)}={len(token_ids)+len(response_ids)}')

# 5. 单轮无缓存: cache_hit_tokens == 0
cache_hit = rec.get('cache_hit_tokens', -1)
if cache_hit != 0:
    errors.append(f'cache_hit_tokens={cache_hit}, 期望=0')

# 6. 存储的model与请求model一致
stored_model = rec.get('model', '')
if stored_model != '${MODEL_NAME}':
    errors.append(f'model不一致: 存储={stored_model}, 请求=${MODEL_NAME}')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print(f'PASS:交叉验证通过, messages含prompt, token_ids={len(token_ids)}, response_ids={len(response_ids)}, cache_hit=0, model={stored_model}')
" 2>/dev/null)

if echo "$CROSSCHECK" | grep -q "^PASS:"; then
    log_success "$CROSSCHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "$CROSSCHECK"
fi

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary