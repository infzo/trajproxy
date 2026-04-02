#!/bin/bash
# ============================================
# 场景四：Tool / Reason 测试
# ============================================
# 测试内容:
#   - OpenAI Tool Call (非流式/流式)
#   - Claude Tool Use (非流式/流式)
#   - Reasoning 提取 (非流式/流式)
#   - 组合测试 (Reasoning + Tool Call)
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/config.sh"

# 测试模型名称
TOOL_MODEL="${TEST_MODEL}_tool_reason"

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

# 步骤 2: 注册带 parser 的模型
test_register_tool_model() {
    log_test "步骤 2: 注册带 parser 的模型"

    local body=$(cat <<EOF
{
  "model_name": "${TOOL_MODEL}",
  "url": "${INFERENCE_URL}",
  "api_key": "${API_KEY}",
  "tokenizer_path": "${TOKENIZER_PATH}",
  "token_in_token_out": true,
  "tool_parser": "${TOOL_PARSER}",
  "reasoning_parser": "${REASONING_PARSER}"
}
EOF
)

    local response
    response=$(http_post "${PROXY_URL}/models/register" "$body" "" "注册 Tool/Reason 模型")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    assert_http_code "$code" "200" "模型注册"
    assert_json_equals "$rbody" "status" "success" "验证注册成功"
    assert_json_equals "$rbody" "detail.tool_parser" "${TOOL_PARSER}" "验证 tool_parser"
    assert_json_equals "$rbody" "detail.reasoning_parser" "${REASONING_PARSER}" "验证 reasoning_parser"
}

# ============================================
# OpenAI Tool Call 测试
# ============================================

# 步骤 3: OpenAI 非流式 Tool Call
test_openai_non_stream_tool_call() {
    log_test "步骤 3: OpenAI 非流式 Tool Call"

    local session_id=$(generate_session_id "tool_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOOL_MODEL}",
  "messages": [
    {"role": "user", "content": "北京今天天气怎么样？"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取指定城市的天气信息",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "城市名称"}
          },
          "required": ["city"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "max_tokens": 200
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "OpenAI 非流式 Tool Call")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "Tool Call 请求"

    # 验证 tool_calls 存在
    local tool_calls=$(json_get "$rbody" "choices[0].message.tool_calls")
    if [[ -n "$tool_calls" && "$tool_calls" != "null" && "$tool_calls" != "[]" ]]; then
        log_success "验证 tool_calls 非空"

        # 验证 tool_calls 结构
        assert_json_equals "$rbody" "choices[0].message.tool_calls[0].type" "function" "验证 tool_calls[0].type"
        assert_json_field "$rbody" "choices[0].message.tool_calls[0].function.name" "验证 function.name"

        # 验证 arguments 是合法 JSON
        local arguments=$(json_get "$rbody" "choices[0].message.tool_calls[0].function.arguments")
        if echo "$arguments" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
            log_success "验证 arguments 是合法 JSON"

            # 验证 arguments 包含 city 字段
            local city=$(echo "$arguments" | python3 -c "import sys,json; print(json.load(sys.stdin).get('city', ''))" 2>/dev/null)
            if [[ -n "$city" ]]; then
                log_success "验证 arguments 包含 city 字段: $city"
            fi
        else
            log_error "arguments 不是合法 JSON: $arguments"
        fi

        # 验证 finish_reason
        assert_json_equals "$rbody" "choices[0].finish_reason" "tool_calls" "验证 finish_reason = tool_calls"
    else
        log_info "注意: 模型未返回 tool_calls（可能直接回复了文本）"
    fi
}

# 步骤 4: OpenAI 流式 Tool Call
test_openai_stream_tool_call() {
    log_test "步骤 4: OpenAI 流式 Tool Call"

    local session_id=$(generate_session_id "tool_002" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOOL_MODEL}",
  "messages": [
    {"role": "user", "content": "查询上海的天气"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取天气",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string"}
          },
          "required": ["city"]
        }
      }
    }
  ],
  "stream": true,
  "max_tokens": 100
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "OpenAI 流式 Tool Call")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "流式 Tool Call 请求"

    # 验证流式 tool_calls
    if echo "$rbody" | grep -q "tool_calls"; then
        log_success "验证流式响应包含 tool_calls"

        # 累积 arguments
        if echo "$rbody" | grep -q "arguments"; then
            log_success "验证流式响应包含 arguments"

            # 验证 finish_reason
            if echo "$rbody" | grep -q "finish_reason.*tool_calls"; then
                log_success "验证 finish_reason = tool_calls"
            fi
        fi
    else
        log_info "注意: 流式响应可能未包含 tool_calls"
    fi
}

# 步骤 5: 工具结果继续对话
test_tool_result_continuation() {
    log_test "步骤 5: 工具结果继续对话"

    local session_id=$(generate_session_id "tool_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOOL_MODEL}",
  "messages": [
    {"role": "user", "content": "北京今天天气怎么样？"},
    {
      "role": "assistant",
      "tool_calls": [
        {
          "id": "call_test_001",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\": \"北京\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_test_001",
      "content": "{\"city\": \"北京\", \"temperature\": 25, \"condition\": \"晴天\"}"
    }
  ],
  "max_tokens": 100
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "工具结果继续对话")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "工具结果对话请求"

    # 验证模型回复
    assert_json_field "$rbody" "choices[0].message.content" "验证 message.content 非空"

    log_info "工具结果继续对话验证完成"
}

# ============================================
# Claude Tool Use 测试
# ============================================

# 步骤 6: Claude 非流式 Tool Use
test_claude_non_stream_tool_use() {
    log_test "步骤 6: Claude 非流式 Tool Use"

    local session_id=$(generate_session_id "claude_tool_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TEST_MODEL}",
  "max_tokens": 200,
  "messages": [
    {"role": "user", "content": "北京的天气怎么样？"}
  ],
  "tools": [
    {
      "name": "get_weather",
      "description": "获取指定城市的天气信息",
      "input_schema": {
        "type": "object",
        "properties": {
          "city": {"type": "string", "description": "城市名称"}
        },
        "required": ["city"]
      }
    }
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
    response=$(http_post "${NGINX_URL}/s/${session_id}/v1/messages" "$body" "$headers" "Claude 非流式 Tool Use")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "Claude Tool Use 请求"

    # 验证 tool_use block
    local content_type=$(json_get "$rbody" "content[0].type")
    if [[ "$content_type" == "tool_use" ]]; then
        log_success "验证 content[0].type = tool_use"

        # 验证 tool_use 结构
        assert_json_equals "$rbody" "content[0].name" "get_weather" "验证 tool name"
        assert_json_field "$rbody" "content[0].input" "验证 tool input 存在"

        # 验证 input.city
        local city=$(json_get "$rbody" "content[0].input.city")
        if [[ -n "$city" ]]; then
            log_success "验证 input.city = $city"
        fi

        # 验证 stop_reason
        assert_json_equals "$rbody" "stop_reason" "tool_use" "验证 stop_reason = tool_use"
    else
        log_info "注意: 模型未返回 tool_use（可能直接回复了文本）"
    fi
}

# 步骤 7: Claude 流式 Tool Use
test_claude_stream_tool_use() {
    log_test "步骤 7: Claude 流式 Tool Use"

    local session_id=$(generate_session_id "claude_tool_002" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TEST_MODEL}",
  "max_tokens": 200,
  "messages": [
    {"role": "user", "content": "查询上海的天气"}
  ],
  "tools": [
    {
      "name": "get_weather",
      "description": "获取天气",
      "input_schema": {
        "type": "object",
        "properties": {
          "city": {"type": "string"}
        },
        "required": ["city"]
      }
    }
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
    response=$(http_post "${NGINX_URL}/s/${session_id}/v1/messages" "$body" "$headers" "Claude 流式 Tool Use")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "Claude 流式 Tool Use 请求"

    # 验证流式 tool_use
    if echo "$rbody" | grep -q "tool_use"; then
        log_success "验证流式响应包含 tool_use"
    else
        log_info "注意: 流式响应可能未包含 tool_use"
    fi
}

# ============================================
# Reasoning 测试
# ============================================

# 步骤 8: 非流式带 Reasoning
test_non_stream_reasoning() {
    log_test "步骤 8: 非流式带 Reasoning"

    local session_id=$(generate_session_id "reason_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOOL_MODEL}",
  "messages": [
    {"role": "user", "content": "请思考一下：1+1等于多少？"}
  ],
  "max_tokens": 200
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "非流式带 Reasoning")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "Reasoning 请求"

    # 验证 reasoning 字段
    local reasoning=$(json_get "$rbody" "choices[0].message.reasoning")
    if [[ -n "$reasoning" && "$reasoning" != "null" ]]; then
        log_success "验证 reasoning 字段存在"
        log_info "Reasoning 内容: ${reasoning:0:50}..."
    else
        log_info "注意: 模型未返回 reasoning 字段（可能未启用或模型不支持）"
    fi

    # 验证 content
    assert_json_field "$rbody" "choices[0].message.content" "验证 message.content 非空"
}

# 步骤 9: 流式带 Reasoning
test_stream_reasoning() {
    log_test "步骤 9: 流式带 Reasoning"

    local session_id=$(generate_session_id "reason_002" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOOL_MODEL}",
  "messages": [
    {"role": "user", "content": "思考并回答：2*3=?"}
  ],
  "stream": true,
  "max_tokens": 100
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "流式带 Reasoning")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "流式 Reasoning 请求"

    # 验证流式 reasoning
    if echo "$rbody" | grep -q "reasoning"; then
        log_success "验证流式响应包含 reasoning 字段"
    else
        log_info "注意: 流式响应可能未包含 reasoning 字段"
    fi

    # 验证 content
    if echo "$rbody" | grep -q "content"; then
        log_success "验证流式响应包含 content"
    fi
}

# ============================================
# 组合测试
# ============================================

# 步骤 10: 组合测试 (Reasoning + Tool Call)
test_combined_reasoning_tool() {
    log_test "步骤 10: 组合测试 (Reasoning + Tool Call)"

    local session_id=$(generate_session_id "combo_001" "task_001")
    local body=$(cat <<EOF
{
  "model": "${TOOL_MODEL}",
  "messages": [
    {"role": "user", "content": "请思考一下北京今天的天气，然后帮我查询。"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取天气",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string"}
          },
          "required": ["city"]
        }
      }
    }
  ],
  "max_tokens": 300
}
EOF
)

    local headers="x-session-id: ${session_id}"

    local response
    response=$(http_post "${PROXY_URL}/v1/chat/completions" "$body" "$headers" "组合测试 (Reasoning + Tool Call)")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "组合测试请求"

    # 验证 reasoning
    local reasoning=$(json_get "$rbody" "choices[0].message.reasoning")
    if [[ -n "$reasoning" && "$reasoning" != "null" ]]; then
        log_success "验证 reasoning 字段存在"
    fi

    # 验证 tool_calls
    local tool_calls=$(json_get "$rbody" "choices[0].message.tool_calls")
    if [[ -n "$tool_calls" && "$tool_calls" != "null" && "$tool_calls" != "[]" ]]; then
        log_success "验证 tool_calls 非空"
        assert_json_equals "$rbody" "choices[0].finish_reason" "tool_calls" "验证 finish_reason = tool_calls"
    fi

    log_info "组合测试验证完成"
}

# ============================================
# 清理和轨迹验证
# ============================================

# 步骤 11: 查询轨迹记录
test_query_trajectory() {
    log_test "步骤 11: 查询轨迹记录"

    local session_id=$(generate_session_id "tool_001" "task_001")

    local response
    response=$(http_get "${PROXY_URL}/trajectory?session_id=${session_id}&limit=10" "查询 Tool Call 轨迹记录")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    # 验证 HTTP 状态码
    assert_http_code "$code" "200" "查询轨迹记录"

    # 验证响应体
    local count=$(json_get "$body" "count")
    if [[ "$count" -gt 0 ]] 2>/dev/null; then
        log_success "验证记录数量 > 0 (实际: $count)"

        # 验证记录详情
        assert_json_field "$body" "records[0].model" "验证记录中的模型名称"
    else
        log_info "注意: 未找到轨迹记录"
    fi
}

# 步骤 12: 删除模型
test_cleanup_model() {
    log_test "步骤 12: 删除模型"

    local response
    response=$(http_delete "${PROXY_URL}/models?model_name=${TOOL_MODEL}" "删除 Tool/Reason 模型")

    local code=$(http_code "$response")
    assert_http_code_in "$code" "200,404" "删除模型"
}

# ============================================
# 主测试流程
# ============================================
main() {
    print_header "场景四：Tool / Reason 测试"

    # 准备阶段
    test_health_check
    test_register_tool_model

    # OpenAI Tool Call 测试
    test_openai_non_stream_tool_call
    test_openai_stream_tool_call
    test_tool_result_continuation

    # Claude Tool Use 测试
    test_claude_non_stream_tool_use
    test_claude_stream_tool_use

    # Reasoning 测试
    test_non_stream_reasoning
    test_stream_reasoning

    # 组合测试
    test_combined_reasoning_tool

    # 清理和验证
    test_query_trajectory
    test_cleanup_model

    # 打印测试摘要
    print_summary "场景四：Tool / Reason 测试"
}

# 运行测试
main
