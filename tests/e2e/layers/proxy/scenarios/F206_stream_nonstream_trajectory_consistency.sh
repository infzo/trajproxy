#!/bin/bash
# 场景 F206: 流式与非流式轨迹一致性验证（Proxy 层）
# 测试流程：注册模型 -> 分别发送相同请求（流式和非流式） -> 查询两条轨迹 -> 比较关键字段一致性 -> 删除模型

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F206: 流式与非流式轨迹一致性验证（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
SCENARIO_ID=$(basename "${BASH_SOURCE[0]}" .sh | grep -oE '[FP][0-9]+' | tr '[:upper:]' '[:lower:]')
CONSISTENCY_TEST_BASE_URL="${BASE_URL}"
CONSISTENCY_TEST_MODEL_NAME="consistency-test-model"
CONSISTENCY_TEST_RUN_ID="run-${SCENARIO_ID}"
CONSISTENCY_TEST_SESSION_ID_STREAM="session-${SCENARIO_ID}-stream-$(date +%s%N | md5sum | head -c 8)"
CONSISTENCY_TEST_SESSION_ID_NONSTREAM="session-${SCENARIO_ID}-nonstream-$(date +%s%N | md5sum | head -c 8)"
CONSISTENCY_TEST_PROMPT="What is 2+2? Answer briefly."
CONSISTENCY_TEST_TOKENIZER_PATH="Qwen/Qwen3.5-2B"

# ========================================
# 步骤 1: 注册模型
# ========================================
log_step "步骤 1: 注册模型（run_id: ${CONSISTENCY_TEST_RUN_ID}, token_in_token_out: true）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CONSISTENCY_TEST_BASE_URL}/models/register' \\
    -H 'Content-Type: application/json' \\
    -d '{
        \"run_id\": \"${CONSISTENCY_TEST_RUN_ID}\",
        \"model_name\": \"${CONSISTENCY_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${CONSISTENCY_TEST_TOKENIZER_PATH}\",
        \"token_in_token_out\": true
    }'"
log_separator

REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CONSISTENCY_TEST_BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"run_id\": \"${CONSISTENCY_TEST_RUN_ID}\",
        \"model_name\": \"${CONSISTENCY_TEST_MODEL_NAME}\",
        \"url\": \"${BACKEND_MODEL_URL}\",
        \"api_key\": \"${TEST_MODEL_API_KEY}\",
        \"tokenizer_path\": \"${CONSISTENCY_TEST_TOKENIZER_PATH}\",
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

sleep 3

echo ""

# ========================================
# 步骤 2: 发送非流式请求
# ========================================
log_step "步骤 2: 发送非流式请求（session_id: ${CONSISTENCY_TEST_SESSION_ID_NONSTREAM}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X POST '${CONSISTENCY_TEST_BASE_URL}/s/${CONSISTENCY_TEST_RUN_ID}/${CONSISTENCY_TEST_SESSION_ID_NONSTREAM}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${CONSISTENCY_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"${CONSISTENCY_TEST_PROMPT}\"}],
        \"stream\": false
    }'"
log_separator

NONSTREAM_CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${CONSISTENCY_TEST_BASE_URL}/s/${CONSISTENCY_TEST_RUN_ID}/${CONSISTENCY_TEST_SESSION_ID_NONSTREAM}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CONSISTENCY_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"${CONSISTENCY_TEST_PROMPT}\"}],
        \"stream\": false
    }")

NONSTREAM_CHAT_BODY=$(echo "$NONSTREAM_CHAT_RESPONSE" | sed '$d')
NONSTREAM_CHAT_STATUS=$(echo "$NONSTREAM_CHAT_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${NONSTREAM_CHAT_STATUS}"
log_response "${NONSTREAM_CHAT_BODY}"
log_separator

assert_http_status "200" "$NONSTREAM_CHAT_STATUS" "非流式请求 HTTP 状态码应为 200"
assert_contains "$NONSTREAM_CHAT_BODY" "choices" "非流式响应应包含 choices 字段"

echo ""

# ========================================
# 步骤 3: 发送流式请求（相同 prompt）
# ========================================
log_step "步骤 3: 发送流式请求（session_id: ${CONSISTENCY_TEST_SESSION_ID_STREAM}）"
log_curl_cmd "curl -s --no-buffer \\
    -X POST '${CONSISTENCY_TEST_BASE_URL}/s/${CONSISTENCY_TEST_RUN_ID}/${CONSISTENCY_TEST_SESSION_ID_STREAM}/v1/chat/completions' \\
    -H 'Content-Type: application/json' \\
    -H 'Authorization: Bearer ${CHAT_API_KEY}' \\
    -d '{
        \"model\": \"${CONSISTENCY_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"${CONSISTENCY_TEST_PROMPT}\"}],
        \"stream\": true
    }'"
log_separator

STREAM_RESPONSE=$(curl -s --no-buffer -X POST "${CONSISTENCY_TEST_BASE_URL}/s/${CONSISTENCY_TEST_RUN_ID}/${CONSISTENCY_TEST_SESSION_ID_STREAM}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{
        \"model\": \"${CONSISTENCY_TEST_MODEL_NAME}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"${CONSISTENCY_TEST_PROMPT}\"}],
        \"stream\": true
    }")

log_response "流式响应内容:"
echo "$STREAM_RESPONSE"
log_separator

assert_contains "$STREAM_RESPONSE" "data:" "流式响应应包含 data: 前缀"
assert_contains "$STREAM_RESPONSE" "[DONE]" "流式响应应以 [DONE] 结束"

echo ""

# ========================================
# 步骤 4: 查询非流式轨迹
# ========================================
log_step "步骤 4: 查询非流式轨迹（session_id: ${CONSISTENCY_TEST_SESSION_ID_NONSTREAM}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${CONSISTENCY_TEST_BASE_URL}/trajectory?session_id=${CONSISTENCY_TEST_SESSION_ID_NONSTREAM}&limit=100'"
log_separator

NONSTREAM_TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${CONSISTENCY_TEST_BASE_URL}/trajectory?session_id=${CONSISTENCY_TEST_SESSION_ID_NONSTREAM}&limit=100")

NONSTREAM_TRAJ_BODY=$(echo "$NONSTREAM_TRAJ_RESPONSE" | sed '$d')
NONSTREAM_TRAJ_STATUS=$(echo "$NONSTREAM_TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${NONSTREAM_TRAJ_STATUS}"
log_response "${NONSTREAM_TRAJ_BODY}"
log_separator

assert_http_status "200" "$NONSTREAM_TRAJ_STATUS" "非流式轨迹查询 HTTP 状态码应为 200"

NONSTREAM_TRAJ_COUNT=$(json_get_number "$NONSTREAM_TRAJ_BODY" "count")
if [ "$NONSTREAM_TRAJ_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "非流式轨迹记录数: ${NONSTREAM_TRAJ_COUNT}"
else
    log_error "非流式轨迹记录数应为大于0，实际为: ${NONSTREAM_TRAJ_COUNT}"
    TEST_FAILED=1
fi

echo ""

# ========================================
# 步骤 5: 查询流式轨迹
# ========================================
log_step "步骤 5: 查询流式轨迹（session_id: ${CONSISTENCY_TEST_SESSION_ID_STREAM}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X GET '${CONSISTENCY_TEST_BASE_URL}/trajectory?session_id=${CONSISTENCY_TEST_SESSION_ID_STREAM}&limit=100'"
log_separator

STREAM_TRAJ_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "${CONSISTENCY_TEST_BASE_URL}/trajectory?session_id=${CONSISTENCY_TEST_SESSION_ID_STREAM}&limit=100")

STREAM_TRAJ_BODY=$(echo "$STREAM_TRAJ_RESPONSE" | sed '$d')
STREAM_TRAJ_STATUS=$(echo "$STREAM_TRAJ_RESPONSE" | sed -n '$p')

log_response "HTTP Status: ${STREAM_TRAJ_STATUS}"
log_response "${STREAM_TRAJ_BODY}"
log_separator

assert_http_status "200" "$STREAM_TRAJ_STATUS" "流式轨迹查询 HTTP 状态码应为 200"

STREAM_TRAJ_COUNT=$(json_get_number "$STREAM_TRAJ_BODY" "count")
if [ "$STREAM_TRAJ_COUNT" -gt 0 ] 2>/dev/null; then
    log_success "流式轨迹记录数: ${STREAM_TRAJ_COUNT}"
else
    log_error "流式轨迹记录数应为大于0，实际为: ${STREAM_TRAJ_COUNT}"
    TEST_FAILED=1
fi

echo ""

# ========================================
# 步骤 6: 比较流式与非流式轨迹一致性
# ========================================
log_step "步骤 6: 比较流式与非流式轨迹字段一致性"
log_separator

# 使用 python3 进行 JSON 级别的字段比较
# 通过临时文件传递 JSON，避免命令行参数长度超限
CONSISTENCY_NS_TMPFILE=$(mktemp)
CONSISTENCY_S_TMPFILE=$(mktemp)
echo "$NONSTREAM_TRAJ_BODY" > "$CONSISTENCY_NS_TMPFILE"
echo "$STREAM_TRAJ_BODY" > "$CONSISTENCY_S_TMPFILE"

CONSISTENCY_RESULT=$(python3 -c '
import json
import sys

# 需要跳过的字段（流式/非流式天然不同，或时序相关）
SKIP_FIELDS = {
    "id", "unique_id", "request_id", "session_id",
    "start_time", "end_time", "processing_duration_ms", "created_at",
}

def is_empty(val):
    """判断值是否为空"""
    return val is None or val == "" or val == [] or val == {}

def compare_recursive(ns_val, s_val, path, errors, info, *,
                      value_eq=False, check_both_nonempty=True):
    """递归比较两个值的一致性

    Args:
        ns_val: 非流式值
        s_val: 流式值
        path: 当前字段路径（用于日志）
        errors: 错误列表
        info: 信息列表
        value_eq: 是否要求值完全相等（标量字段适用）
        check_both_nonempty: 是否检查"两者都非空"
    """
    ns_emp = is_empty(ns_val)
    s_emp = is_empty(s_val)

    # 一方为空一方非空
    if ns_emp and not s_emp:
        errors.append(f"{path}: 非流式为空，流式非空")
        return
    if not ns_emp and s_emp:
        errors.append(f"{path}: 非流式非空，流式为空")
        return
    # 两者都为空
    if ns_emp and s_emp:
        info.append(f"  {path}: 两者均为空 (一致)")
        return

    # 两者都非空 — 检查类型是否一致
    ns_type = type(ns_val).__name__
    s_type = type(s_val).__name__
    if ns_type != s_type:
        errors.append(f"{path}: 类型不一致 (非流式={ns_type}, 流式={s_type})")
        return

    if check_both_nonempty:
        info.append(f"  {path}: 两者均非空")

    # 字典 — 递归检查每个子字段
    if isinstance(ns_val, dict):
        all_keys = sorted(set(ns_val.keys()) | set(s_val.keys()))
        for key in all_keys:
            # 跳过 id/时间类字段
            if key in SKIP_FIELDS:
                continue
            child_path = f"{path}.{key}"
            ns_child = ns_val.get(key)
            s_child = s_val.get(key)
            compare_recursive(ns_child, s_child, child_path, errors, info,
                              value_eq=value_eq, check_both_nonempty=False)

    # 列表 — 检查长度，元素逐个递归比较
    elif isinstance(ns_val, list):
        if len(ns_val) != len(s_val):
            errors.append(f"{path}: 长度不一致 (非流式={len(ns_val)}, 流式={len(s_val)})")
            return
        for i, (ns_item, s_item) in enumerate(zip(ns_val, s_val)):
            child_path = f"{path}[{i}]"
            compare_recursive(ns_item, s_item, child_path, errors, info,
                              value_eq=value_eq, check_both_nonempty=False)

    # 标量 — 可选检查值相等
    elif value_eq:
        if ns_val != s_val:
            errors.append(f"{path}: 值不一致 (非流式={ns_val!r}, 流式={s_val!r})")

def check_field_consistency(nonstream_body, stream_body):
    """检查流式与非流式轨迹记录的关键字段一致性"""
    errors = []
    info = []

    try:
        ns_data = json.loads(nonstream_body)
        s_data = json.loads(stream_body)
    except json.JSONDecodeError as e:
        return [f"JSON解析失败: {e}"], []

    ns_records = ns_data.get("records", [])
    s_records = s_data.get("records", [])

    if not ns_records:
        errors.append("非流式轨迹无记录")
        return errors, info
    if not s_records:
        errors.append("流式轨迹无记录")
        return errors, info

    ns_rec = ns_records[0]
    s_rec = s_records[0]

    # ---- 标量字段：要求值完全相等 ----
    scalar_eq_fields = ["model"]
    for field in scalar_eq_fields:
        compare_recursive(ns_rec.get(field), s_rec.get(field), field,
                          errors, info, value_eq=True)

    # ---- 标量字段：仅检查非空 ----
    nonempty_fields = ["response_text", "response_ids"]
    for field in nonempty_fields:
        compare_recursive(ns_rec.get(field), s_rec.get(field), field,
                          errors, info, value_eq=False)

    # ---- 字典/嵌套字段：递归检查内部子字段 ----
    # messages 内容应完全一致（相同输入 prompt）
    compare_recursive(ns_rec.get("messages"), s_rec.get("messages"),
                      "messages", errors, info, value_eq=True)

    # raw_request 递归检查子字段
    compare_recursive(ns_rec.get("raw_request"), s_rec.get("raw_request"),
                      "raw_request", errors, info)

    # raw_response 递归检查子字段
    compare_recursive(ns_rec.get("raw_response"), s_rec.get("raw_response"),
                      "raw_response", errors, info)

    # token_request 递归检查子字段
    compare_recursive(ns_rec.get("token_request"), s_rec.get("token_request"),
                      "token_request", errors, info)

    # token_response 递归检查子字段
    compare_recursive(ns_rec.get("token_response"), s_rec.get("token_response"),
                      "token_response", errors, info)

    # text_request / text_response 递归检查
    compare_recursive(ns_rec.get("text_request"), s_rec.get("text_request"),
                      "text_request", errors, info)
    compare_recursive(ns_rec.get("text_response"), s_rec.get("text_response"),
                      "text_response", errors, info)

    # prompt_text / token_ids 递归检查
    compare_recursive(ns_rec.get("prompt_text"), s_rec.get("prompt_text"),
                      "prompt_text", errors, info, value_eq=True)
    compare_recursive(ns_rec.get("token_ids"), s_rec.get("token_ids"),
                      "token_ids", errors, info, value_eq=True)

    # ---- token 统计字段 ----
    token_stat_fields = ["prompt_tokens", "completion_tokens", "total_tokens"]
    for field in token_stat_fields:
        ns_has = field in ns_rec and ns_rec[field] is not None
        s_has = field in s_rec and s_rec[field] is not None
        if ns_has and s_has:
            info.append(f"  {field}: 两者均存在 (非流式={ns_rec[field]}, 流式={s_rec[field]})")
        elif not ns_has and not s_has:
            info.append(f"  {field}: 两者均缺失 (一致)")
        else:
            which = "非流式缺失" if not ns_has else "流式缺失"
            errors.append(f"{field}: {which} (不一致)")

    # ---- 检查错误字段 ----
    for label, rec in [("非流式", ns_rec), ("流式", s_rec)]:
        err = rec.get("error")
        if err is not None and err != "":
            errors.append(f"{label}轨迹存在错误: {err}")

    # ---- 检查字段集合一致性（流式不应缺少非流式有的字段） ----
    ns_keys = set(ns_rec.keys()) - SKIP_FIELDS
    s_keys = set(s_rec.keys()) - SKIP_FIELDS
    ns_only = ns_keys - s_keys
    s_only = s_keys - ns_keys
    if ns_only:
        info.append(f"  仅非流式有的字段: {sorted(ns_only)}")
    if s_only:
        info.append(f"  仅流式有的字段: {sorted(s_only)}")

    return errors, info

# 从临时文件读取 JSON
ns_file = sys.argv[1]
s_file = sys.argv[2]
with open(ns_file, "r") as f:
    ns_body = f.read()
with open(s_file, "r") as f:
    s_body = f.read()

errors, info = check_field_consistency(ns_body, s_body)

for line in info:
    print(f"INFO:{line}")
for err in errors:
    print(f"ERROR:{err}")
' "$CONSISTENCY_NS_TMPFILE" "$CONSISTENCY_S_TMPFILE"
)

# 清理临时文件
rm -f "$CONSISTENCY_NS_TMPFILE" "$CONSISTENCY_S_TMPFILE"

# 解析比较结果
CONSISTENCY_ERRORS=$(echo "$CONSISTENCY_RESULT" | grep "^ERROR:" || true)
CONSISTENCY_INFOS=$(echo "$CONSISTENCY_RESULT" | grep "^INFO:" || true)

# 打印详细信息
log_info "一致性检查详情:"
echo "$CONSISTENCY_INFOS" | sed 's/^INFO:/  /'

# 断言检查
if [ -z "$CONSISTENCY_ERRORS" ]; then
    log_success "流式与非流式轨迹关键字段一致性检查通过"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "流式与非流式轨迹存在不一致:"
    echo "$CONSISTENCY_ERRORS" | sed 's/^ERROR:/  /'
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

echo ""

# ========================================
# 步骤 7: 删除模型
# ========================================
log_step "步骤 7: 删除模型（run_id: ${CONSISTENCY_TEST_RUN_ID}）"
log_curl_cmd "curl -s -w '\n%{http_code}' \\
    -X DELETE '${CONSISTENCY_TEST_BASE_URL}/models?model_name=${CONSISTENCY_TEST_MODEL_NAME}&run_id=${CONSISTENCY_TEST_RUN_ID}'"
log_separator

DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${CONSISTENCY_TEST_BASE_URL}/models?model_name=${CONSISTENCY_TEST_MODEL_NAME}&run_id=${CONSISTENCY_TEST_RUN_ID}")

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
