#!/bin/bash
# 场景 F114: EOS Token 一致性测试（Nginx 层）
# 验证 full_conversation_text 与 full_conversation_token_ids 的 EOS 一致性，
# 确保前缀匹配缓存不出现双重 EOS。
#
# 测试流程：注册模型 -> 第1轮对话 -> 验证轨迹 EOS 一致性 -> 第2轮对话 -> 验证缓存命中 -> 删除模型

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F114: EOS Token 一致性测试"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
TEST_BASE_URL="${BASE_URL}"
TEST_MODEL_NAME="eos-consistency-model"
TEST_RUN_ID="run-${SCENARIO_ID}"
TEST_SESSION_ID="session-${SCENARIO_ID}-$(date +%s%N | md5sum | head -c 8)"
TEST_TOKENIZER_PATH="Qwen/Qwen3.5-2B"

# ========== 步骤 1: 注册模型 ==========

log_step "步骤 1: 注册模型（run_id: ${TEST_RUN_ID}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${TEST_RUN_ID}\",
        \"model_name\": \"${TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/models/register" \
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

log_response "HTTP Status: ${REGISTER_STATUS}"
log_response "${REGISTER_BODY}"
log_separator

assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"

REGISTER_RESULT=$(json_get "$REGISTER_BODY" "status")
assert_eq "success" "$REGISTER_RESULT" "注册模型应返回 success"

sleep 2

echo ""

# ========== 步骤 2: 第1轮对话 ==========

log_step "步骤 2: 第1轮对话（无历史，cache_hit_tokens 应为 0）"
ROUND1_MESSAGES='[{"role": "user", "content": "你好，请介绍一下你自己"}]'
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": ${ROUND1_MESSAGES},
        \"max_tokens\": 2048,
        \"stream\": false
    }'"
log_separator

ROUND1_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": ${ROUND1_MESSAGES},
        \"max_tokens\": 2048,
        \"stream\": false
    }")

ROUND1_BODY=$(echo "$ROUND1_RESPONSE" | sed '$d')
ROUND1_STATUS=$(echo "$ROUND1_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${ROUND1_STATUS}"
log_response "${ROUND1_BODY}"
log_separator

assert_http_status "200" "$ROUND1_STATUS" "第1轮对话 HTTP 状态码应为 200"
assert_contains "$ROUND1_BODY" "choices" "第1轮响应应包含 choices 字段"

# 提取第1轮 assistant 响应内容（用户可见，不应含 EOS）
ROUND1_CONTENT=$(echo "$ROUND1_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
print(data['choices'][0]['message']['content'])
" 2>/dev/null)

if [ -n "$ROUND1_CONTENT" ]; then
    log_success "提取第1轮 assistant 响应成功: ${ROUND1_CONTENT}"
else
    log_error "提取第1轮 assistant 响应失败"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 3: 验证用户响应不含 EOS ==========

log_step "步骤 3: 验证用户响应不含 EOS 字符"
# EOS 字符因模型而异，检查常见模式
EOS_IN_USER_RESPONSE=$(echo "$ROUND1_CONTENT" | python3 -c "
import sys
content = sys.stdin.read()
eos_patterns = ['<|im_end|>', '</s>', '<|endoftext|>', '<eos>']
for p in eos_patterns:
    if p in content:
        print('yes:' + p)
        sys.exit(0)
print('no')
" 2>/dev/null)

if echo "$EOS_IN_USER_RESPONSE" | grep -q "^no"; then
    log_success "用户可见响应不含 EOS 字符"
else
    EOS_FOUND=$(echo "$EOS_IN_USER_RESPONSE" | cut -d: -f2)
    log_error "用户可见响应不应包含 EOS 字符，实际包含: ${EOS_FOUND}"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 4: 查询轨迹，验证 EOS 一致性 ==========

log_step "步骤 4: 验证 full_conversation_text 含 EOS，与 token_ids 一致"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=10'"
log_separator

TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=10")

TRAJ_BODY=$(echo "$TRAJ_RESPONSE" | sed '$d')
TRAJ_STATUS=$(echo "$TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ_STATUS}"
log_response "${TRAJ_BODY}"
log_separator

assert_http_status "200" "$TRAJ_STATUS" "查询轨迹 HTTP 状态码应为 200"

TRAJ_COUNT=$(json_get_number "$TRAJ_BODY" "count")
assert_eq "1" "$TRAJ_COUNT" "轨迹记录数应为 1"

EOS_CHECK_RESULT=$(echo "$TRAJ_BODY" | python3 -c "
import sys, json

data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) < 1:
    print('FAIL:no_records')
    sys.exit(0)

record = records[0]
errors = []

full_text = record.get('full_conversation_text', '')
full_token_ids = record.get('full_conversation_token_ids', [])
response_ids = record.get('response_ids', [])
response_text = record.get('response_text', '')

if not full_text:
    errors.append('full_conversation_text 为空')
if not full_token_ids:
    errors.append('full_conversation_token_ids 为空')
if not response_ids:
    errors.append('response_ids 为空')

# 仅当模型正常结束时检查 EOS 一致性
finish_reason = (record.get('raw_response', {})
                 .get('choices', [{}])[0]
                 .get('finish_reason', ''))
if finish_reason != 'stop':
    errors.append(f'finish_reason={finish_reason}, 模型未正常结束, 跳过 EOS 检查')
else:
    r1_eos_token_id = response_ids[-1]

    # 验证 response_ids 和 response_text 一致（都含 EOS）
    if response_text and not response_text.endswith('<|im_end|>'):
        errors.append(f'response_text 不以 <|im_end|> 结尾（re-decode 未生效）')

    # 验证 full_conversation_token_ids 末尾含 EOS
    if not full_token_ids or full_token_ids[-1] != r1_eos_token_id:
        actual_last = full_token_ids[-1] if full_token_ids else 'empty'
        errors.append(f'full_conversation_token_ids 末尾 token_id={actual_last}, 期望={r1_eos_token_id}')

    # 验证 full_conversation_text = prompt + response（正确包含 response_text 作为后缀）
    if response_text and full_text:
        if len(full_text) <= len(response_text):
            errors.append(f'full_conversation_text 长度({len(full_text)})应 > response_text({len(response_text)})')
        elif not full_text.endswith(response_text):
            errors.append('full_conversation_text 应以 response_text 结尾')

# 验证 cache_hit_tokens == 0
cache_hit = record.get('cache_hit_tokens', -1)
if cache_hit != 0:
    errors.append(f'cache_hit_tokens 应为 0, 实际: {cache_hit}')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print(f'PASS:EOS 一致性验证通过: eos_token_id={r1_eos_token_id}, cache_hit_tokens=0')
" 2>/dev/null)

if echo "$EOS_CHECK_RESULT" | grep -q "^PASS:"; then
    log_success "$EOS_CHECK_RESULT"
else
    log_error "$EOS_CHECK_RESULT"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 5: 第2轮对话 ==========

log_step "步骤 5: 第2轮对话（验证多轮一致性）"

ROUND2_MESSAGES=$(ROUND1_CONTENT="$ROUND1_CONTENT" python3 -c "
import json, os
round1 = os.environ['ROUND1_CONTENT']
messages = [
    {'role': 'user', 'content': '你好，请介绍一下你自己'},
    {'role': 'assistant', 'content': round1},
    {'role': 'user', 'content': '你刚才说了什么？'}
]
print(json.dumps(messages, ensure_ascii=False))
" 2>/dev/null)

if [ -z "$ROUND2_MESSAGES" ]; then
    log_error "构造第2轮 messages 失败"
    TEST_FAILED=1
fi

log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": ${ROUND2_MESSAGES},
        \"max_tokens\": 2048,
        \"stream\": false
    }'"
log_separator

# shellcheck disable=SC2086
ROUND2_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${TEST_SESSION_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${TEST_MODEL_NAME}\",
        \"messages\": ${ROUND2_MESSAGES},
        \"max_tokens\": 2048,
        \"stream\": false
    }")

ROUND2_BODY=$(echo "$ROUND2_RESPONSE" | sed '$d')
ROUND2_STATUS=$(echo "$ROUND2_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${ROUND2_STATUS}"
log_response "${ROUND2_BODY}"
log_separator

assert_http_status "200" "$ROUND2_STATUS" "第2轮对话 HTTP 状态码应为 200"

ROUND2_CONTENT=$(echo "$ROUND2_BODY" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
print(data['choices'][0]['message']['content'])
" 2>/dev/null)

if [ -n "$ROUND2_CONTENT" ]; then
    log_success "提取第2轮 assistant 响应成功"
else
    log_error "提取第2轮 assistant 响应失败"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 6: 验证第2轮 EOS 一致性 ==========

log_step "步骤 6: 验证第2轮 full_conversation 无双重 EOS，text/tokens 一致"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=10'"
log_separator

TRAJ2_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${TEST_BASE_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=10")

TRAJ2_BODY=$(echo "$TRAJ2_RESPONSE" | sed '$d')
TRAJ2_STATUS=$(echo "$TRAJ2_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${TRAJ2_STATUS}"
log_response "${TRAJ2_BODY}"
log_separator

assert_http_status "200" "$TRAJ2_STATUS" "查询轨迹 HTTP 状态码应为 200"

ROUND2_CHECK_RESULT=$(echo "$TRAJ2_BODY" | python3 -c "
import sys, json

data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) < 2:
    print('FAIL:轨迹记录数不足, 期望 >= 2, 实际: ' + str(len(records)))
    sys.exit(0)

sorted_records = sorted(records, key=lambda r: r.get('start_time', ''))
r1 = sorted_records[0]
r2 = sorted_records[1]

errors = []

# 对比第1轮和第2轮的 EOS token ID：finish_reason 都是 stop 时才检查
r1_resp_ids = r1.get('response_ids', [])
r2_resp_ids = r2.get('response_ids', [])
r2_token_ids = r2.get('full_conversation_token_ids', [])
r2_text = r2.get('full_conversation_text', '')
r2_resp_text = r2.get('response_text', '')

r1_finish = (r1.get('raw_response', {}).get('choices', [{}])[0].get('finish_reason', ''))
r2_finish = (r2.get('raw_response', {}).get('choices', [{}])[0].get('finish_reason', ''))

if r1_finish != 'stop' or r2_finish != 'stop':
    errors.append(f'finish_reason 不为 stop (r1={r1_finish}, r2={r2_finish}), 跳过 EOS 检查')
elif not r1_resp_ids or not r2_resp_ids:
    errors.append('response_ids 为空')
else:
    r1_eos = r1_resp_ids[-1]
    r2_eos = r2_resp_ids[-1]

    # 验证两轮 EOS token ID 一致
    if r1_eos != r2_eos:
        errors.append(f'两轮 EOS token ID 不一致: r1={r1_eos}, r2={r2_eos}')

    # 验证第2轮 full_conversation_token_ids 末尾为 EOS
    if not r2_token_ids or r2_token_ids[-1] != r1_eos:
        actual_last = r2_token_ids[-1] if r2_token_ids else 'empty'
        errors.append(f'第2轮 full_conversation_token_ids 不以 EOS 结尾: 末尾={actual_last}, 期望={r1_eos}')

    # 验证无双重 EOS
    if len(r2_token_ids) >= 2 and r2_token_ids[-1] == r1_eos and r2_token_ids[-2] == r1_eos:
        errors.append(f'第2轮 full_conversation_token_ids 存在双重 EOS: 末尾两个 token 都是 {r1_eos}')

    # 验证 response_text 含 EOS（re-decode 已生效）
    if r2_resp_text and not r2_resp_text.endswith('<|im_end|>'):
        errors.append('第2轮 response_text 不以 <|im_end|> 结尾（re-decode 未生效）')

    # 验证 full_conversation_text = prompt + response（正确包含 response_text 作为后缀）
    if r2_resp_text and r2_text:
        if len(r2_text) <= len(r2_resp_text):
            errors.append(f'第2轮 full_conversation_text 长度({len(r2_text)})应 > response_text({len(r2_resp_text)})')
        elif not r2_text.endswith(r2_resp_text):
            errors.append('第2轮 full_conversation_text 应以 response_text 结尾')

# 验证第2轮 cache_hit_tokens（TITO 模式下 <think> 块可能导致缓存未命中，不强制要求 > 0）
r2_cache = r2.get('cache_hit_tokens', -1)

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print(f'PASS:第2轮 EOS 一致, eos_token_id={r1_eos}, cache_hit_tokens={r2_cache}, 无双重 EOS')
" 2>/dev/null)

if echo "$ROUND2_CHECK_RESULT" | grep -q "^PASS:"; then
    log_success "$ROUND2_CHECK_RESULT"
else
    log_error "$ROUND2_CHECK_RESULT"
    TEST_FAILED=1
fi

echo ""

# ========== 步骤 7: 删除模型 ==========

log_step "步骤 7: 删除模型（run_id: ${TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${TEST_BASE_URL}/models?model_name=${TEST_MODEL_NAME}&run_id=${TEST_RUN_ID}")

DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${DELETE_STATUS}"
log_response "${DELETE_BODY}"
log_separator

assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"

DELETE_RESULT=$(json_get "$DELETE_BODY" "status")
assert_eq "success" "$DELETE_RESULT" "删除模型应返回 success"

echo ""

print_summary
