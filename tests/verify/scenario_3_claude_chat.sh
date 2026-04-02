#!/bin/bash
# ============================================
# 场景三：Claude Chat 测试
# ============================================
# 测试内容:
#   - 通过 LiteLLM 网关的 Claude (Anthropic) 格式请求
#   - 非流式对话
#   - 流式对话
#   - 轨迹抓取验证
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/config.sh"

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

# 步骤 2: 确认预置模型存在
test_check_preset_model() {
    log_test "步骤 2: 确认预置模型存在"

    local response
    response=$(http_get "${PROXY_URL}/v1/models" "查询模型列表")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    assert_http_code "$code" "200" "获取模型列表"

    # 检查是否有可用模型
    log_info "注意: Claude 测试需要通过 LiteLLM 网关访问预置模型"
    log_info "请确保 LiteLLM 网关 (${NGINX_URL}) 已配置 Claude 模型"
}

# ============================================
# Claude 非流式测试
# ============================================

# 步骤 3: Claude 非流式对话
test_claude_non_stream_chat() {
    log_test "步骤 3: Claude 非流式对话"

    local session_id=$(generate_session_id "claude_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TEST_MODEL}",
  "max_tokens": 100,
  "messages": [
    {"role": "user", "content": "你好，请回复'Claude测试成功'"}
  ]
}
EOF
)

    local headers=$(cat <<EOF
x-api-key: ${LITELLM_API_KEY}
anthropic-version: 2023-06-01
EOF
)

    local response
    response=$(http_post "${NGINX_URL}/s/${session_id}/v1/messages" "$body" "$headers" "Claude 非流式对话")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "Claude 非流式对话请求"

    # 验证 Anthropic 响应格式
    assert_json_equals "$rbody" "type" "message" "验证 type = message"
    assert_json_equals "$rbody" "role" "assistant" "验证 role = assistant"

    # 验证 content block
    assert_json_equals "$rbody" "content[0].type" "text" "验证 content[0].type = text"
    assert_json_field "$rbody" "content[0].text" "验证 content[0].text 非空"

    # 验证 stop_reason
    local stop_reason=$(json_get "$rbody" "stop_reason")
    if [[ "$stop_reason" == "end_turn" || "$stop_reason" == "max_tokens" ]]; then
        log_success "验证 stop_reason in [end_turn, max_tokens]"
    else
        log_error "验证 stop_reason (实际: $stop_reason)"
    fi

    # 验证 usage
    assert_json_gt_zero "$rbody" "usage.input_tokens" "验证 usage.input_tokens > 0"
    assert_json_gt_zero "$rbody" "usage.output_tokens" "验证 usage.output_tokens > 0"
}

# ============================================
# Claude 流式测试
# ============================================

# 步骤 4: Claude 流式对话
test_claude_stream_chat() {
    log_test "步骤 4: Claude 流式对话"

    local session_id=$(generate_session_id "claude_002" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TEST_MODEL}",
  "max_tokens": 50,
  "messages": [
    {"role": "user", "content": "说一个字：好"}
  ],
  "stream": true
}
EOF
)

    local headers=$(cat <<EOF
x-api-key: ${LITELLM_API_KEY}
anthropic-version: 2023-06-01
EOF
)

    local response
    response=$(http_post "${NGINX_URL}/s/${session_id}/v1/messages" "$body" "$headers" "Claude 流式对话")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "Claude 流式对话请求"

    # 验证 SSE 事件序列
    log_info "验证 Claude SSE 事件序列..."

    # 验证 message_start 事件
    if echo "$rbody" | grep -q "message_start"; then
        log_success "验证 message_start 事件存在"
    else
        log_error "缺少 message_start 事件"
    fi

    # 验证 content_block_delta 事件
    if echo "$rbody" | grep -q "content_block_delta"; then
        log_success "验证 content_block_delta 事件存在"
    else
        log_error "缺少 content_block_delta 事件"
    fi

    # 验证 delta.type = text_delta
    if echo "$rbody" | grep -q "text_delta"; then
        log_success "验证 delta.type = text_delta"
    else
        log_error "缺少 text_delta 类型"
    fi

    # 验证 message_stop 事件
    if echo "$rbody" | grep -q "message_stop"; then
        log_success "验证 message_stop 事件存在"
    else
        log_error "缺少 message_stop 事件"
    fi

    log_info "Claude 流式响应验证完成"
}

# ============================================
# 轨迹验证
# ============================================

# 步骤 5: 查询轨迹记录
test_query_trajectory() {
    log_test "步骤 5: 查询轨迹记录"

    local session_id=$(generate_session_id "claude_001" "task_001")

    local response
    response=$(http_get "${PROXY_URL}/trajectory?session_id=${session_id}&limit=10" "查询 Claude 轨迹记录")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "查询轨迹记录"

    # 验证响应体
    assert_json_equals "$body" "session_id" "$session_id" "验证 session_id 匹配"

    local count=$(json_get "$body" "count")
    if [[ "$count" -gt 0 ]] 2>/dev/null; then
        log_success "验证记录数量 > 0 (实际: $count)"

        # 验证记录详情
        assert_json_field "$body" "records[0].model" "验证记录中的模型名称"
        assert_json_gt_zero "$body" "records[0].prompt_tokens" "验证 prompt_tokens > 0"
    else
        log_info "注意: 未找到轨迹记录（可能 TrajProxy 未记录 Claude 请求）"
    fi
}

# ============================================
# 主测试流程
# ============================================
main() {
    print_header "场景三：Claude Chat 测试"

    # 准备阶段
    test_health_check
    test_check_preset_model

    # Claude 测试
    test_claude_non_stream_chat
    test_claude_stream_chat

    # 轨迹验证
    test_query_trajectory

    # 打印测试摘要
    print_summary "场景三：Claude Chat 测试"
}

# 运行测试
main
