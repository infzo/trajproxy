#!/bin/bash
# ============================================
# 场景二：OpenAI Chat 测试
# ============================================
# 测试内容:
#   - 直接转发模式（token_in_token_out=false）
#     - 非流式对话
#     - 流式对话
#   - Token-in-Token-out 模式（token_in_token_out=true）
#     - 非流式对话
#     - 流式对话（带 usage）
#   - 轨迹抓取验证
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/config.sh"

# 测试模型名称
DIRECT_MODEL="${TEST_MODEL}_openai_direct"
TOKEN_MODEL="${TEST_MODEL}_openai_token"

# ============================================
# 准备阶段
# ============================================

# 步骤 1: 健康检查
test_health_check() {
    log_test "步骤 1: 健康检查"

    local response
    response=$(http_get "${PROXY_URL}/health" "健康检查")

    local code=$(http_code "$response")
    assert_http_code "$code" "200" "健康检查"
}

# 步骤 2: 注册直接转发模型
test_register_direct_model() {
    log_test "步骤 2: 注册直接转发模型 (token_in_token_out=false)"

    local body=$(cat <<EOF
{
  "model_name": "${DIRECT_MODEL}",
  "url": "${INFERENCE_URL}",
  "api_key": "${API_KEY}",
  "tokenizer_path": "",
  "token_in_token_out": false
}
EOF
)

    local response
    response=$(http_post "${PROXY_URL}/models/register" "$body" "" "注册直接转发模型")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    assert_http_code "$code" "200" "模型注册"
    assert_json_equals "$rbody" "status" "success" "验证注册成功"
    assert_json_equals "$rbody" "detail.token_in_token_out" "false" "验证 token_in_token_out=false"
}

# ============================================
# 直接转发模式测试
# ============================================

# 步骤 3: 非流式对话（直接转发模式）
test_direct_non_stream_chat() {
    log_test "步骤 3: 非流式对话（直接转发模式）"

    local session_id=$(generate_session_id "sample_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${DIRECT_MODEL}",
  "messages": [
    {"role": "user", "content": "你好，请回复'测试成功'"}
  ],
  "max_tokens": 100,
  "temperature": 0.7
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "非流式对话")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "非流式对话请求"

    # 验证 OpenAI 响应格式
    assert_json_equals "$rbody" "object" "chat.completion" "验证 object 字段"
    assert_json_equals "$rbody" "choices[0].message.role" "assistant" "验证 message.role"
    assert_json_field "$rbody" "choices[0].message.content" "验证 message.content 非空"

    # 验证 finish_reason
    local finish_reason=$(json_get "$rbody" "choices[0].finish_reason")
    if [[ "$finish_reason" == "stop" || "$finish_reason" == "length" ]]; then
        log_success "验证 finish_reason in [stop, length]"
    else
        log_error "验证 finish_reason (实际: $finish_reason)"
    fi

    # 验证 usage
    assert_json_gt_zero "$rbody" "usage.total_tokens" "验证 usage.total_tokens > 0"
}

# 步骤 4: 流式对话（直接转发模式）
test_direct_stream_chat() {
    log_test "步骤 4: 流式对话（直接转发模式）"

    local session_id=$(generate_session_id "sample_002" "task_001")
    local body=$(cat <<EOF
{
  "model": "${DIRECT_MODEL}",
  "messages": [
    {"role": "user", "content": "说一个字：好"}
  ],
  "max_tokens": 10,
  "stream": true
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "流式对话")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "流式对话请求"

    # 验证 Content-Type（这里简化处理，实际需要检查响应头）
    log_info "验证流式响应格式..."

    # 验证 SSE 格式
    if echo "$rbody" | grep -q "data: \[DONE\]"; then
        log_success "验证 SSE 结束标记 [DONE]"
    else
        log_error "缺少 SSE 结束标记 [DONE]"
    fi

    # 验证 delta 内容
    if echo "$rbody" | grep -q "delta.*content"; then
        log_success "验证 delta.content 存在"
    else
        log_error "缺少 delta.content"
    fi

    # 验证 object 类型
    if echo "$rbody" | grep -q "chat.completion.chunk"; then
        log_success "验证 object = chat.completion.chunk"
    else
        log_error "object 应为 chat.completion.chunk"
    fi
}

# 步骤 5: 删除直接转发模型
test_delete_direct_model() {
    log_test "步骤 5: 删除直接转发模型"

    local response
    response=$(http_delete "${PROXY_URL}/models?model_name=${DIRECT_MODEL}" "删除直接转发模型")

    local code=$(http_code "$response")
    assert_http_code_in "$code" "200,404" "删除模型"
}

# ============================================
# Token-in-Token-out 模式测试
# ============================================

# 步骤 6: 注册 Token-in-out 模型
test_register_token_model() {
    log_test "步骤 6: 注册 Token-in-out 模型 (token_in_token_out=true)"

    local body=$(cat <<EOF
{
  "model_name": "${TOKEN_MODEL}",
  "url": "${INFERENCE_URL}",
  "api_key": "${API_KEY}",
  "tokenizer_path": "${TOKENIZER_PATH}",
  "token_in_token_out": true
}
EOF
)

    local response
    response=$(http_post "${PROXY_URL}/models/register" "$body" "" "注册 Token 模式模型")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    assert_http_code "$code" "200" "模型注册"
    assert_json_equals "$rbody" "status" "success" "验证注册成功"
    assert_json_equals "$rbody" "detail.token_in_token_out" "true" "验证 token_in_token_out=true"
    assert_json_field "$rbody" "detail.tokenizer_path" "验证 tokenizer_path 非空"
}

# 步骤 7: 非流式对话（Token 模式）
test_token_non_stream_chat() {
    log_test "步骤 7: 非流式对话（Token 模式）"

    local session_id=$(generate_session_id "token_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOKEN_MODEL}",
  "messages": [
    {"role": "user", "content": "请计算 1+1 等于多少"}
  ],
  "max_tokens": 50
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "非流式对话（Token 模式）")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "非流式对话请求"

    # 验证 OpenAI 响应格式
    assert_json_equals "$rbody" "object" "chat.completion" "验证 object 字段"
    assert_json_field "$rbody" "choices[0].message.content" "验证 message.content 非空"

    # 验证 usage 统计（Token 模式应该更精确）
    assert_json_gt_zero "$rbody" "usage.prompt_tokens" "验证 usage.prompt_tokens > 0"
    assert_json_gt_zero "$rbody" "usage.completion_tokens" "验证 usage.completion_tokens > 0"
    assert_json_gt_zero "$rbody" "usage.total_tokens" "验证 usage.total_tokens > 0"

    log_info "Token 模式提供精确的 token 统计"
}

# 步骤 8: 流式对话（Token 模式，带 usage）
test_token_stream_chat() {
    log_test "步骤 8: 流式对话（Token 模式，带 usage）"

    local session_id=$(generate_session_id "token_002" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOKEN_MODEL}",
  "messages": [
    {"role": "user", "content": "数到三"}
  ],
  "max_tokens": 20,
  "stream": true,
  "stream_options": {"include_usage": true}
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "流式对话（Token 模式，带 usage）")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "流式对话请求"

    # 验证 SSE 格式
    if echo "$rbody" | grep -q "data: \[DONE\]"; then
        log_success "验证 SSE 结束标记 [DONE]"
    else
        log_error "缺少 SSE 结束标记 [DONE]"
    fi

    # 验证 usage 数据块（stream_options.include_usage=true）
    if echo "$rbody" | grep -q "usage.*prompt_tokens"; then
        log_success "验证流式响应包含 usage 统计"
    else
        log_info "注意: 流式响应可能未包含 usage 统计"
    fi

    log_info "Token 模式流式响应验证完成"
}

# 步骤 9: 删除 Token 模式模型
test_delete_token_model() {
    log_test "步骤 9: 删除 Token 模式模型"

    local response
    response=$(http_delete "${PROXY_URL}/models?model_name=${TOKEN_MODEL}" "删除 Token 模式模型")

    local code=$(http_code "$response")
    assert_http_code_in "$code" "200,404" "删除模型"
}

# ============================================
# 轨迹验证
# ============================================

# 步骤 10: 查询轨迹记录
test_query_trajectory() {
    log_test "步骤 10: 查询轨迹记录"

    local session_id=$(generate_session_id "token_001" "task_001")

    local response
    response=$(http_get "${PROXY_URL}/trajectory?session_id=${session_id}&limit=10" "查询轨迹记录")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "查询轨迹记录"

    # 验证响应体
    assert_json_equals "$body" "session_id" "$session_id" "验证 session_id 匹配"
    assert_json_gt_zero "$body" "count" "验证记录数量 > 0"

    # 验证记录详情
    local model=$(json_get "$body" "records[0].model")
    if [[ "$model" == "${TOKEN_MODEL}" ]]; then
        log_success "验证轨迹记录中的模型名称"
    else
        log_info "轨迹记录模型: $model (期望: ${TOKEN_MODEL})"
    fi

    # 验证 token 统计
    assert_json_gt_zero "$body" "records[0].prompt_tokens" "验证 prompt_tokens > 0"

    # Token 模式下应该有 token_ids
    local token_ids=$(json_get "$body" "records[0].token_ids")
    if [[ -n "$token_ids" && "$token_ids" != "null" ]]; then
        log_success "Token 模式下验证 token_ids 存在"
    else
        log_info "注意: token_ids 可能未存储"
    fi
}

# ============================================
# 主测试流程
# ============================================
main() {
    print_header "场景二：OpenAI Chat 测试"

    # 准备阶段
    test_health_check
    test_register_direct_model

    # 直接转发模式测试
    test_direct_non_stream_chat
    test_direct_stream_chat
    test_delete_direct_model

    # Token 模式测试
    test_register_token_model
    test_token_non_stream_chat
    test_token_stream_chat
    test_delete_token_model

    # 轨迹验证
    test_query_trajectory

    # 打印测试摘要
    print_summary "场景二：OpenAI Chat 测试"
}

# 运行测试
main
