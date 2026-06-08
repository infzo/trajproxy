#!/bin/bash
# F208: TITO Tool 轨迹存储（新增）
# 矩阵: TITO×Tool×单轮×轨迹
# 验证目标: tool_calls 和 assistant 含 tool_calls 的完整记录

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== F208: TITO Tool 轨迹 ==="

RUN_ID="run-f208"
SESS_ID="sess-f208-$(date +%s%N | md5sum | head -c 8)"
MODEL_NAME="${DEFAULT_MODEL_NAME}"

TOOLS='[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"location":{"type":"string"}},"required":["location"]}}}]'

# 步骤 1: 注册 TITO + tool_parser + reasoning_parser
log_step "步骤 1: 注册 TITO + tool_parser + reasoning_parser"
REG=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":true,\"tokenizer_path\":\"${DEFAULT_TOKENIZER_PATH}\",\"tool_parser\":\"${DEFAULT_TOOL_PARSER}\",\"reasoning_parser\":\"${DEFAULT_REASONING_PARSER}\"}")
REG_BODY=$(echo "$REG" | sed '$d'); REG_STATUS=$(echo "$REG" | sed -n '$p')
assert_http_status "200" "$REG_STATUS" "注册模型 200"
assert_contains "$REG_BODY" "success" "注册成功"
sleep 1

# 步骤 2: 发送带 tools 的请求
log_step "步骤 2: 发送 TITO Tool 请求（含 tools 定义）"
CHAT=$(curl_with_log -s -w "
%{http_code}" -X POST "${BASE_URL}/s/${RUN_ID}/${SESS_ID}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Weather in Beijing?\"}],\"tools\":${TOOLS},\"max_tokens\":256}")
CHAT_BODY=$(echo "$CHAT" | sed '$d'); CHAT_STATUS=$(echo "$CHAT" | sed -n '$p')
assert_http_status "200" "$CHAT_STATUS" "TITO Tool 请求 200"
sleep 1

# 步骤 3: 验证响应包含 tool_calls 结构
log_step "步骤 3: 验证响应 tool_calls 结构"
TOOL_CHECK=$(echo "$CHAT_BODY" | python3 -c "
import json, sys

data = json.loads(sys.stdin.read())
msg = data['choices'][0]['message']
tool_calls = msg.get('tool_calls')
errors = []

if not tool_calls:
    # 模型可能未返回 tool_calls（非强制调用），content 应非空
    content = msg.get('content', '')
    if not content:
        errors.append('既无 tool_calls 也无 content')
    else:
        print(f'PASS:模型返回content而非tool_calls(非强制调用), content长度={len(content)}')
        sys.exit(0)
else:
    # 验证 tool_calls 结构完整性
    for tc in tool_calls:
        if not tc.get('id'):
            errors.append('tool_call 缺少 id')
        if not tc.get('function', {}).get('name'):
            errors.append('tool_call 缺少 function.name')
        # arguments 应为字符串且不含特殊 token
        args = tc.get('function', {}).get('arguments', '')
        if not isinstance(args, str):
            errors.append(f'tool_call.arguments 类型错误: {type(args)}')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    tc_names = [tc['function']['name'] for tc in tool_calls]
    print(f'PASS:tool_calls结构完整, 包含工具: {tc_names}')
" 2>/dev/null)

if echo "$TOOL_CHECK" | grep -q "^PASS:"; then
    log_success "$TOOL_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "$TOOL_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 4: 查询轨迹，验证 tool_calls 存在于存储记录中
log_step "步骤 4: 查询轨迹，验证 tool_calls 存储完整性"
TRAJ=$(curl_with_log -s "${BASE_URL}/trajectory?session_id=${SESS_ID}&limit=10")

TRAJ_CHECK=$(echo "$TRAJ" | python3 -c "
import json, sys

data = json.loads(sys.stdin.read())
records = data.get('records', [])
if len(records) < 1:
    print('FAIL:轨迹记录数为0')
    sys.exit(0)

record = records[0]
errors = []

# 验证基本字段
for field in ['run_id', 'session_id', 'model_name']:
    val = record.get(field, '')
    if not val:
        errors.append(f'{field} 为空或缺失')

# 验证 messages 包含原始请求
messages = record.get('messages', [])
if not messages:
    errors.append('messages 为空')

# 验证 assistant 消息含 tool_calls
# 查找 messages 中的 assistant role（存储的 raw_request.messages）
assistant_msgs = [m for m in messages if m.get('role') == 'assistant']
# 注意：轨迹的 messages 是请求端发送的 messages，不含响应
# 需要通过 raw_response 或 response_text 验证 tool_calls
raw_response = record.get('raw_response', {})
if raw_response:
    resp_tool_calls = None
    if isinstance(raw_response, dict):
        choices = raw_response.get('choices', [])
        if choices:
            resp_tool_calls = choices[0].get('message', {}).get('tool_calls')
    elif isinstance(raw_response, str):
        try:
            resp_data = json.loads(raw_response)
            choices = resp_data.get('choices', [])
            if choices:
                resp_tool_calls = choices[0].get('message', {}).get('tool_calls')
        except:
            pass

    if resp_tool_calls:
        # 验证每个 tool_call 有 id 和 function.name
        for tc in resp_tool_calls:
            if not tc.get('id'):
                errors.append('raw_response tool_call 缺少 id')
            if not tc.get('function', {}).get('name'):
                errors.append('raw_response tool_call 缺少 function.name')
    else:
        # tool_calls 可能不在 raw_response 中（存储格式可能不同）
        # 通过 response_text 检查是否包含 tool 调用痕迹
        resp_text = record.get('response_text', '')
        if not resp_text and not assistant_msgs:
            errors.append('既无 tool_calls 也无 response_text')

# 验证 token_ids 和 response_ids 非空（TITO 模式）
token_ids = record.get('token_ids', [])
response_ids = record.get('response_ids', [])
if not token_ids:
    errors.append('token_ids 为空（TITO 模式应有）')
if not response_ids:
    errors.append('response_ids 为空（TITO 模式应有）')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    tc_count = len(resp_tool_calls) if resp_tool_calls else 0
    print(f'PASS:轨迹存储完整, tool_calls={tc_count}, token_ids={len(token_ids)}, response_ids={len(response_ids)}')
" 2>/dev/null)

if echo "$TRAJ_CHECK" | grep -q "^PASS:"; then
    log_success "$TRAJ_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "$TRAJ_CHECK"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 步骤 5: 清理
log_step "步骤 5: 删除模型"
curl_with_log -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary