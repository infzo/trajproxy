#!/bin/bash
# Layer 4 utils: 对比测试层工具函数

_LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_LAYER_DIR}/../../utils.sh"
source "${_LAYER_DIR}/config.sh"

COMPARE_PY="${_LAYER_DIR}/compare.py"

# ========================================
# 服务健康检查
# ========================================

# 检查 vLLM 服务是否可用
check_vllm_health() {
    log_info "检查 vLLM 服务健康状态 (${VLLM_URL})..."
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --noproxy '*' --max-time 5 "${VLLM_URL}/health" 2>&1)
    if [ "$http_code" = "200" ]; then
        log_success "vLLM 服务可用"
        return 0
    else
        log_error "vLLM 服务不可用 (HTTP ${http_code})"
        return 1
    fi
}

# 检查 trajproxy 服务是否可用
check_proxy_health() {
    log_info "检查 trajproxy 服务健康状态 (${PROXY_URL})..."
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --noproxy '*' --max-time 5 "${PROXY_URL}/health" 2>&1)
    if [ "$http_code" = "200" ]; then
        log_success "trajproxy 服务可用"
        return 0
    else
        log_error "trajproxy 服务不可用 (HTTP ${http_code})"
        return 1
    fi
}

# ========================================
# 模型注册/删除
# ========================================

# 注册模型到 trajproxy
# 参数: $1=run_id, $2=model_name, $3=token_in_token_out("true"/"false"),
#        $4=tool_parser(可选), $5=reasoning_parser(可选)
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

    REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${PROXY_URL}/models/register" \
        -H "Content-Type: application/json" \
        -d "{
            \"run_id\": \"${run_id}\",
            \"model_name\": \"${model_name}\",
            \"url\": \"${BACKEND_MODEL_URL}\",
            \"api_key\": \"${COMPARISON_CHAT_API_KEY}\",
            \"token_in_token_out\": ${tito}${extra_fields}
        }")

    REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
    REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

    log_response "HTTP Status: ${REGISTER_STATUS}"
    log_response "${REGISTER_BODY}"

    assert_http_status "200" "$REGISTER_STATUS" "注册模型 HTTP 状态码应为 200"

    local register_result=$(json_get "$REGISTER_BODY" "status")
    assert_eq "success" "$register_result" "注册模型应返回 success"

    sleep 1
}

# 删除模型
# 参数: $1=model_name, $2=run_id
delete_model() {
    local model_name="$1"
    local run_id="$2"

    log_step "删除模型: ${model_name} (run_id=${run_id})"

    DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${PROXY_URL}/models?model_name=${model_name}&run_id=${run_id}")

    DELETE_BODY=$(echo "$DELETE_RESPONSE" | sed '$d')
    DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

    log_response "HTTP Status: ${DELETE_STATUS}"
    log_response "${DELETE_BODY}"

    assert_http_status "200" "$DELETE_STATUS" "删除模型 HTTP 状态码应为 200"

    local delete_result=$(json_get "$DELETE_BODY" "status")
    assert_eq "success" "$delete_result" "删除模型应返回 success"
}

# ========================================
# 请求发送
# ========================================

# 发送非流式请求到指定端点，返回响应体临时文件路径
# 参数: $1=endpoint_url, $2=model_name, $3=request_body_json, $4=session_path(可选,如 "s/run_id/sess_id")
send_nonstream_request() {
    local url="$1"
    local model="$2"
    local body="$3"
    local session_path="${4:-}"

    local full_url
    if [ -n "$session_path" ]; then
        full_url="${url}/${session_path}/v1/chat/completions"
    else
        full_url="${url}/v1/chat/completions"
    fi

    local response=$(curl -s --noproxy '*' --max-time 120 -w "\n%{http_code}" \
        -X POST "$full_url" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${COMPARISON_CHAT_API_KEY}" \
        -d "$body")

    local body_part=$(echo "$response" | sed '$d')
    local status=$(echo "$response" | sed -n '$p')

    # 日志输出到 stderr，避免污染 stdout（调用者通过 $(...) 捕获 tmpfile 路径）
    >&2 log_response "HTTP Status: ${status}"
    >&2 log_response "${body_part}"

    # 保存到临时文件供 compare.py 使用
    local tmpfile=$(mktemp)
    echo "$body_part" > "$tmpfile"
    echo "$tmpfile"
}

# 发送流式请求到指定端点，收集所有 SSE chunks
# 参数: $1=endpoint_url, $2=model_name, $3=request_body_json, $4=session_path(可选,如 "s/run_id/sess_id")
send_stream_request() {
    local url="$1"
    local model="$2"
    local body="$3"
    local session_path="${4:-}"

    local full_url
    if [ -n "$session_path" ]; then
        full_url="${url}/${session_path}/v1/chat/completions"
    else
        full_url="${url}/v1/chat/completions"
    fi

    local stream_response=$(curl -s --noproxy '*' --no-buffer --max-time 120 \
        -X POST "$full_url" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${COMPARISON_CHAT_API_KEY}" \
        -d "$body")

    # 保存到临时文件供 compare.py 使用
    local tmpfile=$(mktemp)
    echo "$stream_response" > "$tmpfile"
    echo "$tmpfile"
}

# ========================================
# 对比调用
# ========================================

# 对比两个非流式 JSON 响应
# 参数: $1=vllm_tmpfile, $2=proxy_tmpfile, $3=label
compare_nonstream() {
    local vllm_file="$1"
    local proxy_file="$2"
    local label="$3"

    log_info "对比非流式响应 (${label})..."

    local result=$(python3 "${COMPARE_PY}" \
        --mode nonstream \
        --vllm "$vllm_file" \
        --proxy "$proxy_file" \
        --label "$label" 2>&1)

    echo "$result"

    # 解析结果
    local errors=$(echo "$result" | grep "^ERROR:" || true)
    local infos=$(echo "$result" | grep "^INFO:" || true)

    log_info "对比详情:"
    echo "$infos" | sed 's/^INFO:/  /'

    if [ -z "$errors" ]; then
        log_success "非流式对比通过 (${label})"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_error "非流式对比存在差异 (${label}):"
        echo "$errors" | sed 's/^ERROR:/  /'
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# 对比两个流式 SSE 响应
# 参数: $1=vllm_tmpfile, $2=proxy_tmpfile, $3=label
compare_stream() {
    local vllm_file="$1"
    local proxy_file="$2"
    local label="$3"

    log_info "对比流式响应 (${label})..."

    local result=$(python3 "${COMPARE_PY}" \
        --mode stream \
        --vllm "$vllm_file" \
        --proxy "$proxy_file" \
        --label "$label" 2>&1)

    echo "$result"

    # 解析结果
    local errors=$(echo "$result" | grep "^ERROR:" || true)
    local infos=$(echo "$result" | grep "^INFO:" || true)

    log_info "对比详情:"
    echo "$infos" | sed 's/^INFO:/  /'

    if [ -z "$errors" ]; then
        log_success "流式对比通过 (${label})"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_error "流式对比存在差异 (${label}):"
        echo "$errors" | sed 's/^ERROR:/  /'
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# ========================================
# 常用请求体构建
# ========================================

# 构建纯文本请求体（不含 tools/reasoning）
# 参数: $1=model_name
build_plain_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"What is 2+2? Answer with just the number.\"}],
        \"stream\": false,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 64
    }"
}

# 构建纯文本流式请求体
build_plain_stream_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"What is 2+2? Answer with just the number.\"}],
        \"stream\": true,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 64
    }"
}

# 构建带 tools 的请求体（hermes 格式）
# 参数: $1=model_name
build_tool_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"What is the weather in Beijing today?\"}],
        \"tools\": [
            {
                \"type\": \"function\",
                \"function\": {
                    \"name\": \"get_weather\",
                    \"description\": \"Get the current weather for a location\",
                    \"parameters\": {
                        \"type\": \"object\",
                        \"properties\": {
                            \"location\": {
                                \"type\": \"string\",
                                \"description\": \"The city name\"
                            }
                        },
                        \"required\": [\"location\"]
                    }
                }
            }
        ],
        \"stream\": false,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 256
    }"
}

# 构建带 tools 的流式请求体
build_tool_stream_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"What is the weather in Beijing today?\"}],
        \"tools\": [
            {
                \"type\": \"function\",
                \"function\": {
                    \"name\": \"get_weather\",
                    \"description\": \"Get the current weather for a location\",
                    \"parameters\": {
                        \"type\": \"object\",
                        \"properties\": {
                            \"location\": {
                                \"type\": \"string\",
                                \"description\": \"The city name\"
                            }
                        },
                        \"required\": [\"location\"]
                    }
                }
            }
        ],
        \"stream\": true,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 256
    }"
}

# 构建带 reasoning 的请求体（引导模型输出思考内容）
# 参数: $1=model_name
build_reasoning_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Solve: if x+3=7, what is x? Think step by step.\"}],
        \"stream\": false,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 256
    }"
}

# 构建带 reasoning 的流式请求体
build_reasoning_stream_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Solve: if x+3=7, what is x? Think step by step.\"}],
        \"stream\": true,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 256
    }"
}

# 构建带 reasoning + tools 的请求体
# 参数: $1=model_name
build_reasoning_tool_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"I need to know the current weather in Shanghai. Think about what information you need, then call the appropriate tool.\"}],
        \"tools\": [
            {
                \"type\": \"function\",
                \"function\": {
                    \"name\": \"get_weather\",
                    \"description\": \"Get the current weather for a location\",
                    \"parameters\": {
                        \"type\": \"object\",
                        \"properties\": {
                            \"location\": {
                                \"type\": \"string\",
                                \"description\": \"The city name\"
                            }
                        },
                        \"required\": [\"location\"]
                    }
                }
            }
        ],
        \"stream\": false,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 512
    }"
}

# 构建带 reasoning + tools 的流式请求体
build_reasoning_tool_stream_request_body() {
    local model="$1"
    echo "{
        \"model\": \"${model}\",
        \"messages\": [{\"role\": \"user\", \"content\": \"I need to know the current weather in Shanghai. Think about what information you need, then call the appropriate tool.\"}],
        \"tools\": [
            {
                \"type\": \"function\",
                \"function\": {
                    \"name\": \"get_weather\",
                    \"description\": \"Get the current weather for a location\",
                    \"parameters\": {
                        \"type\": \"object\",
                        \"properties\": {
                            \"location\": {
                                \"type\": \"string\",
                                \"description\": \"The city name\"
                            }
                        },
                        \"required\": [\"location\"]
                    }
                }
            }
        ],
        \"stream\": true,
        ${COMPARISON_SAMPLING_PARAMS},
        \"max_tokens\": 512
    }"
}