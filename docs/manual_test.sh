#!/bin/bash
# ============================================
# TrajProxy 手动测试脚本
# ============================================
# 用途: 部署后核心功能验证
# 使用: bash manual_test.sh [options]
# 选项:
#   --proxy-url    TrajProxy 地址 (默认: http://localhost:12300)
#   --nginx-url    Nginx 网关地址 (默认: http://localhost:12345)
#   --model        测试模型名称 (默认使用已有模型)
#   --skip-register 跳过模型注册 (使用已有模型)
#   --token-mode   使用 Token-in-Token-out 模式测试工具调用解析
#   --verbose      显示详细请求/响应信息
#   --help         显示帮助信息
# ============================================

# 注意：不使用 set -e，因为我们需要在测试失败时继续执行

# ============================================
# 配置区域 - 可通过环境变量或参数覆盖
# ============================================
PROXY_URL="${PROXY_URL:-http://localhost:12300}"
NGINX_URL="${NGINX_URL:-http://localhost:12345}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-1234}"
TEST_MODEL=""
SKIP_REGISTER=false
VERBOSE=false
TOKEN_MODE=false

# 测试配置
TEST_RUN_ID="manual_test_$(date +%Y%m%d_%H%M%S)"
TEST_SESSION_ID="${TEST_RUN_ID},sample_001,task_001"

# 测试结果统计
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0
SKIPPED_TESTS=0

# ============================================
# 颜色定义
# ============================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color
BOLD='\033[1m'
DIM='\033[2m'

# ============================================
# 辅助函数
# ============================================

print_header() {
    echo ""
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo ""
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
    PASSED_TESTS=$((PASSED_TESTS + 1))
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAILED_TESTS=$((FAILED_TESTS + 1))
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
}

log_skip() {
    echo -e "${YELLOW}[SKIP]${NC} $1"
    SKIPPED_TESTS=$((SKIPPED_TESTS + 1))
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
}

log_test() {
    echo -e "${BOLD}  → $1${NC}"
}

# 打印请求详情
print_request() {
    local method="$1"
    local url="$2"
    local headers="$3"
    local body="$4"

    echo -e "${DIM}────────────────────────────────────────${NC}"
    echo -e "${MAGENTA}请求:${NC}"
    echo -e "${CYAN}  Method:${NC} $method"
    echo -e "${CYAN}  URL:${NC}    $url"
    if [[ -n "$headers" ]]; then
        echo -e "${CYAN}  Headers:${NC}"
        echo "$headers" | while read -r line; do
            echo -e "    $line"
        done
    fi
    if [[ -n "$body" ]]; then
        echo -e "${CYAN}  Body:${NC}"
        echo "$body" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))" 2>/dev/null | while read -r line; do
            echo -e "    $line"
        done || echo -e "    $body"
    fi
    echo -e "${DIM}────────────────────────────────────────${NC}"
}

# 打印响应详情
print_response() {
    local code="$1"
    local body="$2"

    echo -e "${DIM}────────────────────────────────────────${NC}"
    echo -e "${MAGENTA}响应:${NC}"
    echo -e "${CYAN}  Status:${NC} HTTP $code"
    echo -e "${CYAN}  Body:${NC}"
    echo "$body" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))" 2>/dev/null | while read -r line; do
        echo -e "    $line"
    done || echo -e "    $body"
    echo -e "${DIM}────────────────────────────────────────${NC}"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "缺少必要命令: $1"
        exit 1
    fi
}

http_code() {
    echo "$1" | tail -n 1
}

response_body() {
    echo "$1" | sed '$d'
}

# ============================================
# 解析命令行参数
# ============================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --proxy-url)
                PROXY_URL="$2"
                shift 2
                ;;
            --nginx-url)
                NGINX_URL="$2"
                shift 2
                ;;
            --model)
                TEST_MODEL="$2"
                shift 2
                ;;
            --skip-register)
                SKIP_REGISTER=true
                shift
                ;;
            --token-mode)
                TOKEN_MODE=true
                shift
                ;;
            --verbose|-v)
                VERBOSE=true
                shift
                ;;
            --help|-h)
                echo "用法: $0 [选项]"
                echo ""
                echo "选项:"
                echo "  --proxy-url URL     TrajProxy 地址 (默认: http://localhost:12300)"
                echo "  --nginx-url URL     Nginx 网关地址 (默认: http://localhost:12345)"
                echo "  --model NAME        测试模型名称"
                echo "  --skip-register     跳过模型注册，使用已有模型"
                echo "  --token-mode        使用 Token-in-Token-out 模式测试工具调用解析"
                echo "  --verbose, -v       显示详细请求/响应信息"
                echo "  --help              显示此帮助信息"
                echo ""
                echo "环境变量:"
                echo "  PROXY_URL           TrajProxy 地址"
                echo "  NGINX_URL           Nginx 网关地址"
                echo "  LITELLM_API_KEY     LiteLLM API 密钥"
                echo ""
                echo "模式说明:"
                echo "  直接转发模式 (默认): token_in_token_out=false，不解析工具调用"
                echo "  Token模式 (--token-mode): token_in_token_out=true，解析工具调用"
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                exit 1
                ;;
        esac
    done
}

# ============================================
# 测试函数
# ============================================

# 1. 健康检查
test_health() {
    print_header "1. 健康检查"

    log_test "检查 TrajProxy 服务状态..."
    local url="${PROXY_URL}/health"
    local response
    response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null || echo -e "\n000")
    local code=$(http_code "$response")
    local body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "GET" "$url" "" ""
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "TrajProxy 服务正常 (HTTP $code)"
    else
        log_error "TrajProxy 服务异常 (HTTP $code)"
        log_info "请确认服务已启动: $PROXY_URL"
        exit 1
    fi

    log_test "检查 Nginx 网关状态..."
    url="${NGINX_URL}/health"
    response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null || echo -e "\n000")
    code=$(http_code "$response")
    body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "GET" "$url" "" ""
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "Nginx 网关正常 (HTTP $code)"
    else
        log_error "Nginx 网关异常 (HTTP $code)"
        log_info "注意: 部分测试可能失败"
    fi
}

# 2. 模型管理
test_model_management() {
    print_header "2. 模型管理"

    # 2.1 查看已有模型
    log_test "查看已有模型列表..."
    local url="${PROXY_URL}/v1/models"
    local response
    response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
    local code=$(http_code "$response")
    local body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "GET" "$url" "" ""
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "获取模型列表成功 (HTTP $code)"
        local model_count=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))" 2>/dev/null || echo "0")
        log_info "当前模型数量: $model_count"

        # 如果没有指定模型，尝试使用第一个已有模型
        if [[ -z "$TEST_MODEL" && "$model_count" -gt 0 ]]; then
            TEST_MODEL=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null)
            if [[ -n "$TEST_MODEL" ]]; then
                log_info "使用已有模型: $TEST_MODEL"
            fi
        fi
    else
        log_error "获取模型列表失败 (HTTP $code)"
    fi

    # 2.2 注册测试模型
    if [[ "$SKIP_REGISTER" == true ]]; then
        log_skip "跳过模型注册 (--skip-register)"
        if [[ -z "$TEST_MODEL" ]]; then
            log_error "未指定测试模型，请使用 --model 参数"
            exit 1
        fi
        return
    fi

    # 使用已有的测试模型名或创建新的
    if [[ -z "$TEST_MODEL" ]]; then
        TEST_MODEL="test_model_${TEST_RUN_ID}"
    fi

    log_test "注册测试模型: $TEST_MODEL..."
    url="${PROXY_URL}/models/register"
    local request_data='{
        "model_name": "'"$TEST_MODEL"'",
        "url": "http://localhost:1234",
        "api_key": "sk-test-key",
        "tokenizer_path": "Qwen/Qwen2.5-3B",
        "token_in_token_out": false
    }'

    local headers="Content-Type: application/json"
    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -d "$request_data" 2>/dev/null)
    code=$(http_code "$response")
    body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "$headers" "$request_data"
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "模型注册成功 (HTTP $code)"
    elif [[ "$code" == "400" || "$code" == "409" ]]; then
        log_info "模型已存在，继续测试 (HTTP $code)"
    else
        log_error "模型注册失败 (HTTP $code)"
    fi
}

# 3. OpenAI Chat 测试
test_openai_chat() {
    print_header "3. OpenAI Chat 测试"

    if [[ -z "$TEST_MODEL" ]]; then
        log_skip "未配置测试模型"
        return
    fi

    # 3.1 非流式请求
    log_test "非流式请求测试..."
    local url="${PROXY_URL}/v1/chat/completions"
    local request_data='{
        "model": "'"$TEST_MODEL"'",
        "messages": [{"role": "user", "content": "你好，请回复\"测试成功\""}],
        "max_tokens": 50,
        "temperature": 0.7
    }'

    local headers="Content-Type: application/json\nx-session-id: ${TEST_SESSION_ID}"
    local response
    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "x-session-id: ${TEST_SESSION_ID}" \
        -d "$request_data" 2>/dev/null)
    local code=$(http_code "$response")
    local body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "$headers" "$request_data"
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "OpenAI 非流式请求成功 (HTTP $code)"
    else
        log_error "OpenAI 非流式请求失败 (HTTP $code)"
        log_info "注意: 如果模型未连接实际推理服务，此测试会失败"
    fi

    # 3.2 流式请求
    log_test "流式请求测试..."
    request_data='{
        "model": "'"${TEST_MODEL}"'",
        "messages": [{"role": "user", "content": "说一个字：好"}],
        "max_tokens": 10,
        "stream": true
    }'
    headers="Content-Type: application/json\nx-session-id: ${TEST_SESSION_ID}"

    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "x-session-id: ${TEST_SESSION_ID}" \
        -d "$request_data" 2>/dev/null)
    code=$(http_code "$response")
    body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "$headers" "$request_data"
        # 流式响应只显示前几行
        local body_preview=$(echo "$body" | head -20)
        print_response "$code" "$body_preview"
    fi

    if [[ "$code" == "200" ]]; then
        if echo "$body" | grep -q "\[DONE\]"; then
            log_success "OpenAI 流式请求成功 (HTTP $code)"
        else
            log_info "流式请求返回数据，但未检测到 [DONE] 标记"
        fi
    else
        log_error "OpenAI 流式请求失败 (HTTP $code)"
    fi

    # 3.3 通过路径传递 session_id
    log_test "通过路径传递 session_id..."
    url="${PROXY_URL}/s/${TEST_SESSION_ID}/v1/chat/completions"
    request_data='{
        "model": "'"${TEST_MODEL}"'",
        "messages": [{"role": "user", "content": "测试路径传参"}],
        "max_tokens": 20
    }'

    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -d "$request_data" 2>/dev/null)
    code=$(http_code "$response")
    body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "Content-Type: application/json" "$request_data"
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "路径传递 session_id 成功 (HTTP $code)"
    else
        log_error "路径传递 session_id 失败 (HTTP $code)"
    fi
}

# 4. Claude Chat 测试
test_claude_chat() {
    print_header "4. Claude Chat 测试 (通过 LiteLLM)"

    if [[ -z "$TEST_MODEL" ]]; then
        log_skip "未配置测试模型"
        return
    fi

    # 4.1 非流式请求
    log_test "Claude 非流式请求测试..."
    local url="${NGINX_URL}/s/${TEST_SESSION_ID}/v1/messages"
    local request_data='{
        "model": "'"$TEST_MODEL"'",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "你好，请回复\"测试成功\""}]
    }'

    local headers="Content-Type: application/json\nx-api-key: ${LITELLM_API_KEY}\nanthropic-version: 2023-06-01"
    local response
    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${LITELLM_API_KEY}" \
        -H "anthropic-version: 2023-06-01" \
        -d "$request_data" 2>/dev/null)
    local code=$(http_code "$response")
    local body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "$headers" "$request_data"
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "Claude 非流式请求成功 (HTTP $code)"
    else
        log_error "Claude 非流式请求失败 (HTTP $code)"
        log_info "注意: 如果 LiteLLM 未配置，此测试会失败"
    fi

    # 4.2 流式请求
    log_test "Claude 流式请求测试..."
    request_data='{
        "model": "'"${TEST_MODEL}"'",
        "max_tokens": 20,
        "messages": [{"role": "user", "content": "说一个字：好"}],
        "stream": true
    }'

    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${LITELLM_API_KEY}" \
        -H "anthropic-version: 2023-06-01" \
        -d "$request_data" 2>/dev/null)
    code=$(http_code "$response")
    body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "$headers" "$request_data"
        local body_preview=$(echo "$body" | head -20)
        print_response "$code" "$body_preview"
    fi

    if [[ "$code" == "200" ]]; then
        if echo "$body" | grep -q "message_stop"; then
            log_success "Claude 流式请求成功 (HTTP $code)"
        else
            log_info "流式请求返回数据"
        fi
    else
        log_error "Claude 流式请求失败 (HTTP $code)"
    fi
}

# 5. Tool Call 测试
test_tool_call() {
    print_header "5. Tool Call 测试"

    if [[ -z "$TEST_MODEL" ]]; then
        log_skip "未配置测试模型"
        return
    fi

    # 显示当前测试模式
    if [[ "$TOKEN_MODE" == true ]]; then
        log_info "Token-in-Token-out 模式：工具调用将被解析为标准格式"
    else
        log_info "直接转发模式：工具调用不会被解析，模型返回原始文本"
    fi

    # 5.1 OpenAI 格式 Tool Calling
    log_test "OpenAI 格式 Tool Calling..."
    local url="${PROXY_URL}/v1/chat/completions"
    local tool_definition='[
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
    ]'

    local request_data='{
        "model": "'"$TEST_MODEL"'",
        "messages": [{"role": "user", "content": "北京今天天气怎么样？"}],
        "tools": '"$tool_definition"',
        "tool_choice": "auto",
        "max_tokens": 200
    }'

    local headers="Content-Type: application/json\nx-session-id: ${TEST_SESSION_ID}"
    local response
    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "x-session-id: ${TEST_SESSION_ID}" \
        -d "$request_data" 2>/dev/null)
    local code=$(http_code "$response")
    local body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "$headers" "$request_data"
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "OpenAI Tool Calling 请求成功 (HTTP $code)"

        # 解析响应
        local parse_result=$(echo "$body" | python3 -c "
import sys,json
d=json.load(sys.stdin)
msg=d.get('choices',[{}])[0].get('message',{})
finish_reason=d.get('choices',[{}])[0].get('finish_reason','')

has_tool_calls='yes' if 'tool_calls' in msg and msg['tool_calls'] else 'no'
tool_call_count=len(msg.get('tool_calls',[]))

# 检查第一个工具调用
first_tool_name=''
first_tool_args=''
if tool_call_count > 0:
    tc=msg['tool_calls'][0]
    first_tool_name=tc.get('function',{}).get('name','')
    first_tool_args=tc.get('function',{}).get('arguments','')

print(f'{has_tool_calls}|{tool_call_count}|{finish_reason}|{first_tool_name}|{first_tool_args}')
" 2>/dev/null || echo "parse_error|0|||")

        local has_tool_calls=$(echo "$parse_result" | cut -d'|' -f1)
        local tool_call_count=$(echo "$parse_result" | cut -d'|' -f2)
        local finish_reason=$(echo "$parse_result" | cut -d'|' -f3)
        local first_tool_name=$(echo "$parse_result" | cut -d'|' -f4)
        local first_tool_args=$(echo "$parse_result" | cut -d'|' -f5)

        if [[ "$has_tool_calls" == "yes" ]]; then
            log_info "模型返回了 $tool_call_count 个 tool_calls"
            log_info "finish_reason: $finish_reason"
            if [[ -n "$first_tool_name" ]]; then
                log_info "工具名称: $first_tool_name"
            fi
            if [[ -n "$first_tool_args" ]]; then
                log_info "工具参数: $first_tool_args"
            fi

            # Token-in-Token-out 模式下验证格式
            if [[ "$TOKEN_MODE" == true ]]; then
                if [[ "$finish_reason" == "tool_calls" ]]; then
                    log_success "finish_reason 正确为 'tool_calls'"
                else
                    log_error "finish_reason 应为 'tool_calls'，实际为 '$finish_reason'"
                fi
            fi
        else
            if [[ "$TOKEN_MODE" == true ]]; then
                log_info "模型未返回 tool_calls（可能是内容回复或模型不支持）"
            else
                log_info "直接转发模式：模型返回原始内容，tool_calls 未解析"
            fi
        fi
    else
        log_error "OpenAI Tool Calling 请求失败 (HTTP $code)"
    fi

    # 5.2 Claude 格式 Tool Use
    log_test "Claude 格式 Tool Use..."
    local claude_tool='[
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
    ]'

    url="${NGINX_URL}/s/${TEST_SESSION_ID}/v1/messages"
    request_data='{
        "model": "'"${TEST_MODEL}"'",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": "北京今天天气怎么样？"}],
        "tools": '"$claude_tool"'
    }'
    headers="Content-Type: application/json\nx-api-key: ${LITELLM_API_KEY}\nanthropic-version: 2023-06-01"

    response=$(curl -s -w "\n%{http_code}" -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${LITELLM_API_KEY}" \
        -H "anthropic-version: 2023-06-01" \
        -d "$request_data" 2>/dev/null)
    code=$(http_code "$response")
    body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "POST" "$url" "$headers" "$request_data"
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "Claude Tool Use 请求成功 (HTTP $code)"
    else
        log_error "Claude Tool Use 请求失败 (HTTP $code)"
    fi
}

# 6. 轨迹查询
test_trajectory() {
    print_header "6. 轨迹查询"

    log_test "查询请求轨迹..."
    local url="${PROXY_URL}/trajectory?session_id=${TEST_SESSION_ID}&limit=10"
    local response
    response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
    local code=$(http_code "$response")
    local body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "GET" "$url" "" ""
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "轨迹查询成功 (HTTP $code)"
        local count=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count', 0))" 2>/dev/null || echo "0")
        log_info "查询到 $count 条记录"
    else
        log_error "轨迹查询失败 (HTTP $code)"
    fi
}

# 7. 清理
test_cleanup() {
    print_header "7. 清理测试资源"

    if [[ "$SKIP_REGISTER" == true ]]; then
        log_skip "跳过模型清理 (--skip-register)"
        return
    fi

    if [[ -z "$TEST_MODEL" ]]; then
        log_skip "无测试模型需要清理"
        return
    fi

    log_test "删除测试模型: $TEST_MODEL..."
    local url="${PROXY_URL}/models?model_name=${TEST_MODEL}"
    local response
    response=$(curl -s -w "\n%{http_code}" -X DELETE "$url" 2>/dev/null)
    local code=$(http_code "$response")
    local body=$(response_body "$response")

    if [[ "$VERBOSE" == true ]]; then
        print_request "DELETE" "$url" "" ""
        print_response "$code" "$body"
    fi

    if [[ "$code" == "200" ]]; then
        log_success "模型删除成功 (HTTP $code)"
    elif [[ "$code" == "404" ]]; then
        log_info "模型已不存在 (HTTP $code)"
    else
        log_error "模型删除失败 (HTTP $code)"
    fi
}

# ============================================
# 打印测试摘要
# ============================================
print_summary() {
    print_header "测试摘要"

    echo -e "  总测试数:  ${BOLD}${TOTAL_TESTS}${NC}"
    echo -e "  ${GREEN}通过: ${PASSED_TESTS}${NC}"
    echo -e "  ${RED}失败: ${FAILED_TESTS}${NC}"
    echo -e "  ${YELLOW}跳过: ${SKIPPED_TESTS}${NC}"
    echo ""

    if [[ $FAILED_TESTS -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}✓ 所有测试通过！${NC}"
    else
        echo -e "${RED}${BOLD}✗ 部分测试失败${NC}"
    fi

    echo ""
    echo "测试配置:"
    echo "  - TrajProxy: $PROXY_URL"
    echo "  - Nginx: $NGINX_URL"
    echo "  - 测试模型: ${TEST_MODEL:-未指定}"
    echo "  - Session ID: $TEST_SESSION_ID"
    echo "  - 测试模式: $([ "$TOKEN_MODE" == true ] && echo "Token-in-Token-out (工具调用解析)" || echo "直接转发 (无工具调用解析)")"
    echo ""
}

# ============================================
# 主函数
# ============================================
main() {
    echo ""
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${CYAN}║          TrajProxy 手动测试脚本                           ║${NC}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # 检查必要命令
    check_command curl
    check_command python3

    # 解析参数
    parse_args "$@"

    # 显示配置
    log_info "测试运行 ID: $TEST_RUN_ID"
    log_info "TrajProxy 地址: $PROXY_URL"
    log_info "Nginx 地址: $NGINX_URL"
    if [[ "$TOKEN_MODE" == true ]]; then
        log_info "测试模式: Token-in-Token-out (工具调用解析)"
    else
        log_info "测试模式: 直接转发 (无工具调用解析)"
    fi
    if [[ "$VERBOSE" == true ]]; then
        log_info "详细模式: 已启用"
    fi

    # 执行测试
    test_health
    test_model_management
    test_openai_chat
    test_claude_chat
    test_tool_call
    test_trajectory
    test_cleanup

    # 打印摘要
    print_summary
}

# 执行主函数
main "$@"
