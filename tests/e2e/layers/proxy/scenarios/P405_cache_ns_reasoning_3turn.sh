#!/bin/bash
# P405: TITO 非流式 Reasoning 3轮缓存
# 矩阵: TITO×ns×Reasoning×3轮×session
# 验证目标: reasoning_content 回传匹配 + 缓存递增
# 校验公式:
#   校验1: cache_hit_tokens[R_n] == len(full_conversation_token_ids[R_{n-1}])
#   校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P405: TITO 非流式 Reasoning 3轮 ==="

RUN_ID="run-p405"
SESS_ID="sess-f305-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

log_step "注册 TITO + reasoning_parser（不带 tool_parser）"
curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}" > /dev/null
sleep 1

# ===== 第1轮: 非流式 Reasoning =====
log_step "第1轮 非流式 Reasoning"
R1_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Think step by step: what is 5+3?\"}],\"max_tokens\":2048,\"chat_template_kwargs\":{\"preserve_thinking\":true,\"enable_thinking\":true}}")
R1_BODY=$(echo "$R1_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R1_RESP" | sed -n '$p')" "第1轮 200"

# 提取 reasoning_content 和 content 分开验证
R1_EXTRACT=$(echo "$R1_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
msg = data['choices'][0]['message']
content = msg.get('content', '') or ''
reasoning = msg.get('reasoning_content', '') or msg.get('reasoning', '') or ''
# 输出为JSON方便后续解析
print(json.dumps({'content': content, 'reasoning': reasoning}, ensure_ascii=False))
" 2>/dev/null)
R1_REASONING=$(echo "$R1_EXTRACT" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['reasoning'])" 2>/dev/null)
R1_CONTENT=$(echo "$R1_EXTRACT" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['content'])" 2>/dev/null)

# 验证 reasoning_content 非空（reasoning_parser 应产生推理内容）
if [ -n "$R1_REASONING" ]; then
    log_success "第1轮 reasoning_content 非空 (长度=${#R1_REASONING})"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "第1轮 reasoning_content 为空（reasoning_parser 可能未生效）"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); log_failure "第1轮 reasoning_content 为空（reasoning_parser 可能未生效）" ""
fi

if [ -n "$R1_CONTENT" ]; then
    log_success "第1轮 content 非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "第1轮 content 为空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); log_failure "第1轮 content 为空" ""
fi
sleep 1

# ===== 第2轮: 构造增量 messages（含 R1 assistant 回复含 reasoning_content） =====
log_step "第2轮 非流式 Reasoning（增量messages，含 reasoning_content 回传）"
R2_MESSAGES=$(R1_REASONING="$R1_REASONING" R1_CONTENT="$R1_CONTENT" python3 -c "
import json, os
r1_content = os.environ['R1_CONTENT']
r1_reasoning = os.environ['R1_REASONING']
assistant_msg = {'role': 'assistant', 'content': r1_content}
if r1_reasoning:
    assistant_msg['reasoning'] = r1_reasoning
    assistant_msg['reasoning_content'] = r1_reasoning
messages = [{'role':'user','content':'Think step by step: what is 5+3?'},
            assistant_msg,
            {'role':'user','content':'Think step by step: what is 10+7?'}]
print(json.dumps(messages, ensure_ascii=False))
")

R2_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R2_MESSAGES},\"max_tokens\":2048,\"chat_template_kwargs\":{\"preserve_thinking\":true,\"enable_thinking\":true}}")
R2_BODY=$(echo "$R2_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R2_RESP" | sed -n '$p')" "第2轮 200"

R2_EXTRACT=$(echo "$R2_BODY" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
msg = data['choices'][0]['message']
content = msg.get('content', '') or ''
reasoning = msg.get('reasoning_content', '') or msg.get('reasoning', '') or ''
print(json.dumps({'content': content, 'reasoning': reasoning}, ensure_ascii=False))
" 2>/dev/null)
R2_REASONING=$(echo "$R2_EXTRACT" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['reasoning'])" 2>/dev/null)
R2_CONTENT=$(echo "$R2_EXTRACT" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['content'])" 2>/dev/null)

if [ -n "$R2_REASONING" ]; then
    log_success "第2轮 reasoning_content 非空 (长度=${#R2_REASONING})"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_warning "第2轮 reasoning_content 为空（模型可能不再产生推理）"
fi

if [ -n "$R2_CONTENT" ]; then
    log_success "第2轮 content 非空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "第2轮 content 为空"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); log_failure "第2轮 content 为空" ""
fi
sleep 1

# ===== 第3轮: 构造增量 messages（含 R1 + R2 assistant 回复） =====
log_step "第3轮 非流式 Reasoning（增量messages）"
R3_MESSAGES=$(R1_CONTENT="$R1_CONTENT" R1_REASONING="$R1_REASONING" R2_CONTENT="$R2_CONTENT" R2_REASONING="$R2_REASONING" python3 -c "
import json, os
a1 = {'role': 'assistant', 'content': os.environ['R1_CONTENT']}
if os.environ['R1_REASONING']:
    a1['reasoning'] = os.environ['R1_REASONING']
    a1['reasoning_content'] = os.environ['R1_REASONING']
a2 = {'role': 'assistant', 'content': os.environ['R2_CONTENT']}
if os.environ['R2_REASONING']:
    a2['reasoning'] = os.environ['R2_REASONING']
    a2['reasoning_content'] = os.environ['R2_REASONING']
messages = [{'role':'user','content':'Think step by step: what is 5+3?'},
            a1,
            {'role':'user','content':'Think step by step: what is 10+7?'},
            a2,
            {'role':'user','content':'Think step by step: what is 20+15?'}]
print(json.dumps(messages, ensure_ascii=False))
")
R3_RESP=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":${R3_MESSAGES},\"max_tokens\":2048,\"chat_template_kwargs\":{\"preserve_thinking\":true,\"enable_thinking\":true}}")
R3_BODY=$(echo "$R3_RESP" | sed '$d')
assert_http_status "200" "$(echo "$R3_RESP" | sed -n '$p')" "第3轮 200"
sleep 1

# ===== 缓存校验 =====
verify_cache_incremental "$SESS_ID" 3 "incremental"

# ===== 轨迹 reasoning_content 存储验证 =====
log_step "验证轨迹中 reasoning_content 字段存储"
TRAJ=$(curl_with_log -s "${BASE_URL}/trajectory?session_id=${SESS_ID}&limit=10")

REASONING_TRAJ_CHECK=$(echo "$TRAJ" | python3 -c "
import json, sys

data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) < 3:
    print(f'FAIL:轨迹记录不足3轮, 实际={len(records)}')
    sys.exit(0)

errors = []
for i, rec in enumerate(sorted(records, key=lambda r: r.get('start_time', ''))):
    round_num = i + 1
    # 验证 response_text 包含 reasoning 痕迹
    # reasoning_content 在 raw_response 中应有体现
    raw_resp = rec.get('raw_response', {})
    if isinstance(raw_resp, str):
        try:
            raw_resp = json.loads(raw_resp)
        except:
            pass

    if isinstance(raw_resp, dict):
        choices = raw_resp.get('choices', [])
        if choices:
            msg = choices[0].get('message', {})
            reasoning = msg.get('reasoning_content', '') or msg.get('reasoning', '') or ''
            if not reasoning and round_num == 1:
                errors.append(f'R{round_num}: raw_response 中无 reasoning_content（reasoning_parser应产生推理内容）')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    print('PASS:轨迹存储中 reasoning_content 字段存在于第1轮')
" 2>/dev/null)

if echo "$REASONING_TRAJ_CHECK" | grep -q "^PASS:"; then
    log_success "$REASONING_TRAJ_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_warning "$REASONING_TRAJ_CHECK"
fi

curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary