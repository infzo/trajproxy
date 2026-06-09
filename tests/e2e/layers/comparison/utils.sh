#!/bin/bash
# Comparison Layer 工具函数

_LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_LAYER_DIR}/../../utils.sh"
source "${_LAYER_DIR}/config.sh"

COMPARE_PY="${_LAYER_DIR}/compare.py"

# ========================================
# 服务健康检查
# ========================================

check_vllm_health() {
    log_info "检查 vLLM 服务健康状态 (${VLLM_URL})..."
    local http_code
    http_code=$(command curl -s -o /dev/null -w "%{http_code}" --noproxy '*' --max-time 5 "${VLLM_URL}/health" 2>&1)
    if [ "$http_code" = "200" ]; then
        log_success "vLLM 服务可用"
        return 0
    else
        log_error "vLLM 服务不可用 (HTTP ${http_code})"
        return 1
    fi
}

check_proxy_health() {
    log_info "检查 trajproxy 服务健康状态 (${PROXY_URL})..."
    local http_code
    http_code=$(command curl -s -o /dev/null -w "%{http_code}" --noproxy '*' --max-time 5 "${PROXY_URL}/health" 2>&1)
    if [ "$http_code" = "200" ]; then
        log_success "trajproxy 服务可用"
        return 0
    else
        log_error "trajproxy 服务不可用 (HTTP ${http_code})"
        return 1
    fi
}

check_nginx_health() {
    log_info "检查 NGINX 服务健康状态 (${NGINX_URL})..."
    local http_code
    http_code=$(command curl -s -o /dev/null -w "%{http_code}" --noproxy '*' --max-time 5 "${NGINX_URL}/health" 2>&1)
    if [ "$http_code" = "200" ]; then
        log_success "NGINX 服务可用"
        return 0
    else
        log_error "NGINX 服务不可用 (HTTP ${http_code})"
        return 1
    fi
}

# ========================================
# 模型注册/删除
# ========================================

register_model() {
    local run_id="$1"
    local model_name="$2"
    local tito="$3"
    local tool_parser="${4:-}"
    local reasoning_parser="${5:-}"

    local extra_fields=""
    if [ -n "$tool_parser" ]; then
        extra_fields="${extra_fields}, \"tool_parser\": \"${tool_parser}\""
    fi
    if [ -n "$reasoning_parser" ]; then
        extra_fields="${extra_fields}, \"reasoning_parser\": \"${reasoning_parser}\""
    fi
    if [ "$tito" = "true" ]; then
        extra_fields="${extra_fields}, \"tokenizer_path\": \"${COMPARISON_TOKENIZER_PATH}\""
    fi

    log_step "注册模型: ${model_name} (run_id=${run_id}, tito=${tito}${extra_fields})"

    REGISTER_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X POST "${PROXY_URL}/models/register" \
        -H "Content-Type: application/json" \
        -d "{
            \"run_id\": \"${run_id}\",
            \"model_name\": \"${model_name}\",
            \"url\": \"${BACKEND_MODEL_URL}\",
            \"api_key\": \"${COMPARISON_API_KEY}\",
            \"token_in_token_out\": ${tito}${extra_fields}
        }")

    REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
    REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

    assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"
    local register_result=$(json_get "$REGISTER_BODY" "status")
    assert_eq "success" "$register_result" "注册模型应返回 success"
    sleep 1
}

delete_model() {
    local model_name="$1"
    local run_id="$2"

    log_step "删除模型: ${model_name} (run_id=${run_id})"
    DELETE_RESPONSE=$(curl_with_log -s -w "\n%{http_code}" -X DELETE "${PROXY_URL}/models?model_name=${model_name}&run_id=${run_id}")
    DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
    DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

    assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"
    local delete_result=$(json_get "$DELETE_BODY" "status")
    assert_eq "success" "$delete_result" "删除模型应返回 success"
}

# ========================================
# OpenAI 格式请求发送
# ========================================

send_openai_nonstream() {
    local url="$1"
    local body="$2"
    local session_path="${3:-}"

    local full_url
    if [ -n "$session_path" ]; then
        full_url="${url}/${session_path}/v1/chat/completions"
    else
        full_url="${url}/v1/chat/completions"
    fi

    local response=$(curl_with_log -s --noproxy '*' --max-time 120 -w "\n%{http_code}" \
        -X POST "$full_url" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${COMPARISON_API_KEY}" \
        -d "$body")

    local body_part=$(echo "$response" | sed '$d')

    local tmpfile=$(mktemp)
    echo "$body_part" > "$tmpfile"
    echo "$tmpfile"
}

send_openai_stream() {
    local url="$1"
    local body="$2"
    local session_path="${3:-}"

    local full_url
    if [ -n "$session_path" ]; then
        full_url="${url}/${session_path}/v1/chat/completions"
    else
        full_url="${url}/v1/chat/completions"
    fi

    local tmpfile=$(mktemp)

    curl_with_log -s --no-buffer --noproxy '*' --max-time 120 \
        -X POST "$full_url" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${COMPARISON_API_KEY}" \
        -d "$body" > "$tmpfile"

    echo "$tmpfile"
}

# ========================================
# Claude 格式请求发送
# ========================================

send_claude_nonstream() {
    local url="$1"
    local body="$2"
    local session_path="${3:-}"

    local full_url
    if [ -n "$session_path" ]; then
        full_url="${url}/${session_path}/v1/messages"
    else
        full_url="${url}/v1/messages"
    fi

    local response=$(curl_with_log -s --noproxy '*' --max-time 120 -w "\n%{http_code}" \
        -X POST "$full_url" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${COMPARISON_API_KEY}" \
        -H "anthropic-version: 2023-06-01" \
        -d "$body")

    local body_part=$(echo "$response" | sed '$d')

    local tmpfile=$(mktemp)
    echo "$body_part" > "$tmpfile"
    echo "$tmpfile"
}

send_claude_stream() {
    local url="$1"
    local body="$2"
    local session_path="${3:-}"

    local full_url
    if [ -n "$session_path" ]; then
        full_url="${url}/${session_path}/v1/messages"
    else
        full_url="${url}/v1/messages"
    fi

    local tmpfile=$(mktemp)

    curl_with_log -s --no-buffer --noproxy '*' --max-time 120 \
        -X POST "$full_url" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${COMPARISON_API_KEY}" \
        -H "anthropic-version: 2023-06-01" \
        -d "$body" > "$tmpfile"

    echo "$tmpfile"
}

# ========================================
# 对比函数
# ========================================

compare_openai_nonstream() {
    local vllm_file="$1"
    local proxy_file="$2"
    local label="$3"
    local has_reasoning="${4:-false}"
    local reasoning_flag=""
    [[ "$has_reasoning" == "true" ]] && reasoning_flag="--with-reasoning"
    log_info "OpenAI 非流式对比 (${label})..."
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if ! python3 "${COMPARE_PY}" --mode nonstream --api openai --vllm "$vllm_file" --proxy "$proxy_file" --label "$label" $reasoning_flag; then
        TESTS_FAILED=$((TESTS_FAILED + 1))
        log_failure "OpenAI 非流式对比失败 (${label})" "详见上方 ERROR 输出"
        return 1
    fi
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

compare_openai_stream() {
    local vllm_file="$1"
    local proxy_file="$2"
    local label="$3"
    local has_reasoning="${4:-false}"
    local reasoning_flag=""
    [[ "$has_reasoning" == "true" ]] && reasoning_flag="--with-reasoning"
    log_info "OpenAI 流式对比 (${label})..."
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if ! python3 "${COMPARE_PY}" --mode stream --api openai --vllm "$vllm_file" --proxy "$proxy_file" --label "$label" $reasoning_flag; then
        TESTS_FAILED=$((TESTS_FAILED + 1))
        log_failure "OpenAI 流式对比失败 (${label})" "详见上方 ERROR 输出"
        return 1
    fi
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

compare_claude_nonstream() {
    local vllm_file="$1"
    local proxy_file="$2"
    local label="$3"
    log_info "Claude 非流式对比 (${label})..."
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if ! python3 "${COMPARE_PY}" --mode nonstream --api claude --vllm "$vllm_file" --proxy "$proxy_file" --label "$label"; then
        TESTS_FAILED=$((TESTS_FAILED + 1))
        log_failure "Claude 非流式对比失败 (${label})" "详见上方 ERROR 输出"
        return 1
    fi
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

compare_claude_stream() {
    local vllm_file="$1"
    local proxy_file="$2"
    local label="$3"
    log_info "Claude 流式对比 (${label})..."
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if ! python3 "${COMPARE_PY}" --mode stream --api claude --vllm "$vllm_file" --proxy "$proxy_file" --label "$label"; then
        TESTS_FAILED=$((TESTS_FAILED + 1))
        log_failure "Claude 流式对比失败 (${label})" "详见上方 ERROR 输出"
        return 1
    fi
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

# ========================================
# OpenAI 请求体构建
# ========================================

build_openai_plain_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in one sentence.\"}],\"stream\":false,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":128,\"chat_template_kwargs\":{\"enable_thinking\":false}}"
}

build_openai_plain_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in one sentence.\"}],\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":128,\"chat_template_kwargs\":{\"enable_thinking\":false}}"
}

build_openai_tool_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Beijing?\"}],\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"description\":\"Get weather\",\"parameters\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}}],\"stream\":false,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":512,\"chat_template_kwargs\":{\"enable_thinking\":false}}"
}

build_openai_tool_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Beijing?\"}],\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"description\":\"Get weather\",\"parameters\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}}],\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":512,\"chat_template_kwargs\":{\"enable_thinking\":false}}"
}

build_openai_reasoning_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"Solve: if x+3=7, what is x? Think step by step.\"}],\"stream\":false,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":256,\"chat_template_kwargs\":{\"preserve_thinking\":true,\"enable_thinking\":true}}"
}

build_openai_reasoning_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"Solve: if x+3=7, what is x? Think step by step.\"}],\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":256,\"chat_template_kwargs\":{\"preserve_thinking\":true,\"enable_thinking\":true}}"
}

build_openai_reasoning_tool_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"I need the weather in Shanghai. Think first, then call the tool.\"}],\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"description\":\"Get weather\",\"parameters\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}}],\"stream\":false,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":1024,\"chat_template_kwargs\":{\"preserve_thinking\":true,\"enable_thinking\":true}}"
}

build_openai_reasoning_tool_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"I need the weather in Shanghai. Think first, then call the tool.\"}],\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"description\":\"Get weather\",\"parameters\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}}],\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"max_tokens\":1024,\"chat_template_kwargs\":{\"preserve_thinking\":true,\"enable_thinking\":true}}"
}

# ========================================
# Claude 请求体构建（/v1/messages 格式）
# ========================================

build_claude_plain_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":128,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in one sentence.\"}]}"
}

build_claude_plain_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":128,\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in one sentence.\"}]}"
}

build_claude_tool_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":512,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Beijing?\"}],\"tools\":[{\"name\":\"get_weather\",\"description\":\"Get weather\",\"input_schema\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}]}"
}

build_claude_tool_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":512,\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Beijing?\"}],\"tools\":[{\"name\":\"get_weather\",\"description\":\"Get weather\",\"input_schema\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}]}"
}

build_claude_reasoning_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":256,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"Solve: if x+3=7, what is x? Think step by step.\"}]}"
}

build_claude_reasoning_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":256,\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"Solve: if x+3=7, what is x? Think step by step.\"}]}"
}

build_claude_reasoning_tool_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":1024,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"I need the weather in Shanghai. Think first, then call the tool.\"}],\"tools\":[{\"name\":\"get_weather\",\"description\":\"Get weather\",\"input_schema\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}]}"
}

build_claude_reasoning_tool_stream_request() {
    local model="$1"
    echo "{\"model\":\"${model}\",\"max_tokens\":1024,\"stream\":true,${COMPARISON_SAMPLING_PARAMS},\"messages\":[{\"role\":\"user\",\"content\":\"I need the weather in Shanghai. Think first, then call the tool.\"}],\"tools\":[{\"name\":\"get_weather\",\"description\":\"Get weather\",\"input_schema\":{\"type\":\"object\",\"properties\":{\"location\":{\"type\":\"string\"}},\"required\":[\"location\"]}}]}"
}
