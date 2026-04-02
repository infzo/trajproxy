#!/bin/bash
# ============================================
# TrajProxy 验证测试 - 公共函数库
# ============================================
# 用途: 提供所有场景测试脚本共用的函数和配置
# 使用: source tests/verify/common.sh
# ============================================

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
# 默认配置
# ============================================
PROXY_URL="${PROXY_URL:-http://localhost:12300}"
NGINX_URL="${NGINX_URL:-http://localhost:12345}"
INFERENCE_URL="${INFERENCE_URL:-http://localhost:8000/v1}"
API_KEY="${API_KEY:-sk-test-key}"
VERBOSE="${VERBOSE:-true}"

# 测试运行ID
TEST_RUN_ID="verify_$(date +%Y%m%d_%H%M%S)"

# 测试结果统计
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0
SKIPPED_TESTS=0

# ============================================
# 输出函数
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
    echo -e "${BOLD}${YELLOW}  ▶ $1${NC}"
}

# ============================================
# 格式化输出函数
# ============================================

# 打印 curl 原始命令
print_curl_command() {
    local method="$1"
    local url="$2"
    local headers="$3"
    local body="$4"

    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}[CURL 命令]${NC}"
    echo -e "${DIM}"

    local cmd="curl -s -w '\\n%{http_code}' -X $method '$url'"

    if [[ -n "$headers" ]]; then
        echo "$headers" | while IFS= read -r line; do
            if [[ -n "$line" ]]; then
                cmd="$cmd -H '$line'"
            fi
        done
    fi

    if [[ -n "$body" ]]; then
        cmd="$cmd -H 'Content-Type: application/json' -d '$body'"
    fi

    echo "$cmd"
    echo -e "${NC}"
}

# 打印请求详情
print_request() {
    local method="$1"
    local url="$2"
    local headers="$3"
    local body="$4"

    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}[请求信息]${NC}"
    echo -e "${CYAN}  Method:${NC} $method"
    echo -e "${CYAN}  URL:${NC}    $url"
    if [[ -n "$headers" ]]; then
        echo -e "${CYAN}  Headers:${NC}"
        echo "$headers" | while IFS= read -r line; do
            if [[ -n "$line" ]]; then
                echo -e "    $line"
            fi
        done
    fi
    if [[ -n "$body" ]]; then
        echo -e "${CYAN}  Body:${NC}"
        echo "$body" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))" 2>/dev/null | while IFS= read -r line; do
            echo -e "    $line"
        done || echo -e "    $body"
    fi
    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# 打印响应详情
print_response() {
    local code="$1"
    local body="$2"

    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}[响应信息]${NC}"
    echo -e "${CYAN}  HTTP Status:${NC} $code"
    echo -e "${CYAN}  Body:${NC}"

    # 尝试格式化 JSON
    local formatted
    formatted=$(echo "$body" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))" 2>/dev/null)

    if [[ -n "$formatted" ]]; then
        echo "$formatted" | while IFS= read -r line; do
            echo -e "    $line"
        done
    else
        # 如果不是 JSON 或格式化失败，直接输出（处理 SSE 流式响应）
        echo "$body" | while IFS= read -r line; do
            echo -e "    $line"
        done
    fi
    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# ============================================
# HTTP 辅助函数
# ============================================

# 从 curl 响应中提取 HTTP 状态码
http_code() {
    echo "$1" | tail -n 1
}

# 从 curl 响应中提取响应体
response_body() {
    echo "$1" | sed '$d'
}

# 执行 HTTP GET 请求
# 用法: http_get <url> [description]
http_get() {
    local url="$1"
    local description="${2:-GET $url}"

    log_test "$description"

    # 打印 curl 命令
    print_curl_command "GET" "$url" "" ""

    # 执行请求
    local response
    response=$(curl -s -w "\n%{http_code}" -X GET "$url" 2>/dev/null || echo -e "\n000")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    # 打印响应
    print_response "$code" "$body"

    echo "$response"
}

# 执行 HTTP POST 请求
# 用法: http_post <url> <body> [headers] [description]
http_post() {
    local url="$1"
    local body="$2"
    local headers="$3"
    local description="${4:-POST $url}"

    log_test "$description"

    # 打印 curl 命令
    print_curl_command "POST" "$url" "$headers" "$body"

    # 构建 curl 命令
    local curl_headers="-H 'Content-Type: application/json'"
    if [[ -n "$headers" ]]; then
        while IFS= read -r line; do
            if [[ -n "$line" ]]; then
                curl_headers="$curl_headers -H '$line'"
            fi
        done <<< "$headers"
    fi

    # 执行请求
    local response
    response=$(eval "curl -s -w '\n%{http_code}' -X POST '$url' $curl_headers -d '$body'" 2>/dev/null || echo -e "\n000")

    local code=$(http_code "$response")
    local rbody=$(response_body "$response")

    # 打印响应
    print_response "$code" "$rbody"

    echo "$response"
}

# 执行 HTTP DELETE 请求
# 用法: http_delete <url> [description]
http_delete() {
    local url="$1"
    local description="${2:-DELETE $url}"

    log_test "$description"

    # 打印 curl 命令
    print_curl_command "DELETE" "$url" "" ""

    # 执行请求
    local response
    response=$(curl -s -w "\n%{http_code}" -X DELETE "$url" 2>/dev/null || echo -e "\n000")

    local code=$(http_code "$response")
    local body=$(response_body "$response")

    # 打印响应
    print_response "$code" "$body"

    echo "$response"
}

# ============================================
# 测试摘要
# ============================================

print_summary() {
    local scenario_name="$1"

    echo ""
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo -e "${BOLD}${CYAN}  测试摘要: ${scenario_name}${NC}"
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo ""

    echo -e "  总测试数:  ${BOLD}${TOTAL_TESTS}${NC}"
    echo -e "  ${GREEN}通过: ${PASSED_TESTS}${NC}"
    echo -e "  ${RED}失败: ${FAILED_TESTS}${NC}"
    echo -e "  ${YELLOW}跳过: ${SKIPPED_TESTS}${NC}"
    echo ""

    if [[ $FAILED_TESTS -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}✓ 所有测试通过！${NC}"
        return 0
    else
        echo -e "${RED}${BOLD}✗ 部分测试失败${NC}"
        return 1
    fi
    echo ""
}

# ============================================
# JSON 验证辅助函数
# ============================================

# 从 JSON 中提取字段值（支持简单路径如 "choices[0].message.content"）
json_get() {
    local json="$1"
    local path="$2"
    echo "$json" | python3 -c "
import sys, json
def get_nested(obj, path):
    parts = path.replace(']', '').replace('[', '.').split('.')
    for p in parts:
        if not p:
            continue
        if isinstance(obj, dict):
            obj = obj.get(p)
        elif isinstance(obj, list):
            try:
                obj = obj[int(p)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if obj is None:
            return None
    return obj

d = json.load(sys.stdin)
result = get_nested(d, '$path')
if result is None:
    print('')
elif isinstance(result, (dict, list)):
    print(json.dumps(result, ensure_ascii=False))
else:
    print(result)
" 2>/dev/null
}

# 验证 HTTP 状态码
# 返回: 0=成功, 1=失败
assert_http_code() {
    local actual="$1"
    local expected="$2"
    local description="$3"

    if [[ "$actual" == "$expected" ]]; then
        log_success "$description (HTTP $actual)"
        return 0
    else
        log_error "$description (期望 HTTP $expected, 实际 HTTP $actual)"
        return 1
    fi
}

# 验证 HTTP 状态码在列表中
# 返回: 0=成功, 1=失败
assert_http_code_in() {
    local actual="$1"
    local expected_list="$2"
    local description="$3"

    local found=false
    IFS=',' read -ra codes <<< "$expected_list"
    for code in "${codes[@]}"; do
        if [[ "$actual" == "$code" ]]; then
            found=true
            break
        fi
    done

    if [[ "$found" == true ]]; then
        log_success "$description (HTTP $actual)"
        return 0
    else
        log_error "$description (期望 HTTP in [$expected_list], 实际 HTTP $actual)"
        return 1
    fi
}

# 验证 JSON 字段存在且非空
# 返回: 0=成功, 1=失败
assert_json_field() {
    local json="$1"
    local path="$2"
    local description="$3"

    local value
    value=$(json_get "$json" "$path")

    if [[ -z "$value" || "$value" == "null" ]]; then
        log_error "$description (字段 '$path' 为空或不存在)"
        return 1
    else
        log_success "$description"
        return 0
    fi
}

# 验证 JSON 字段值等于预期
# 返回: 0=成功, 1=失败
assert_json_equals() {
    local json="$1"
    local path="$2"
    local expected="$3"
    local description="$4"

    local value
    value=$(json_get "$json" "$path")

    if [[ "$value" == "$expected" ]]; then
        log_success "$description"
        return 0
    else
        log_error "$description (期望: '$expected', 实际: '$value')"
        return 1
    fi
}

# 验证 JSON 字段包含预期字符串
# 返回: 0=成功, 1=失败
assert_json_contains() {
    local json="$1"
    local path="$2"
    local expected="$3"
    local description="$4"

    local value
    value=$(json_get "$json" "$path")

    if [[ "$value" == *"$expected"* ]]; then
        log_success "$description"
        return 0
    else
        log_error "$description (期望包含: '$expected', 实际: '$value')"
        return 1
    fi
}

# 验证 JSON 字段值大于 0
# 返回: 0=成功, 1=失败
assert_json_gt_zero() {
    local json="$1"
    local path="$2"
    local description="$3"

    local value
    value=$(json_get "$json" "$path")

    if [[ -n "$value" && "$value" -gt 0 ]] 2>/dev/null; then
        log_success "$description"
        return 0
    else
        log_error "$description (字段 '$path' 应大于 0, 实际: '$value')"
        return 1
    fi
}

# ============================================
# 检查必要命令
# ============================================

check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "缺少必要命令: $1"
        exit 1
    fi
}

# 检查所有必要命令
check_dependencies() {
    check_command "curl"
    check_command "python3"
    log_info "依赖检查通过"
}

# ============================================
# Session ID 生成函数
# ============================================

# 生成 Session ID
# 用法: generate_session_id <sample_id> [task_id]
generate_session_id() {
    local sample_id="${1:-sample_001}"
    local task_id="${2:-task_001}"
    echo "${TEST_RUN_ID},${sample_id},${task_id}"
}

# ============================================
# 配置显示函数
# ============================================

# 显示配置信息
show_config() {
    echo -e "${CYAN}当前测试配置:${NC}"
    echo -e "  PROXY_URL:         ${PROXY_URL}"
    echo -e "  NGINX_URL:         ${NGINX_URL}"
    echo -e "  INFERENCE_URL:     ${INFERENCE_URL}"
    echo -e "  API_KEY:           ${API_KEY}"
    echo -e "  TEST_MODEL:        ${TEST_MODEL}"
    echo -e "  TOKENIZER_PATH:    ${TOKENIZER_PATH}"
    echo -e "  TEST_RUN_ID:       ${TEST_RUN_ID}"
    echo ""
}

# ============================================
# JSON 字段验证辅助函数
# ============================================

# 验证 JSON 字段存在且非空（简化版）
# 返回: 0=成功, 1=失败
assert_json_field() {
    local json="$1"
    local path="$2"
    local description="$3"

    local value
    value=$(json_get "$json" "$path")

    if [[ -z "$value" || "$value" == "null" || "$value" == "[]" ]]; then
        log_error "$description (字段 '$path' 为空或不存在)"
        return 1
    else
        log_success "$description"
        return 0
    fi
}

# 验证 JSON 字段值大于 0
# 返回: 0=成功, 1=失败
assert_json_gt_zero() {
    local json="$1"
    local path="$2"
    local description="$3"

    local value
    value=$(json_get "$json" "$path")

    if [[ -n "$value" && "$value" =~ ^[0-9]+$ && "$value" -gt 0 ]] 2>/dev/null; then
        log_success "$description"
        return 0
    else
        log_error "$description (字段 '$path' 应大于 0, 实际: '$value')"
        return 1
    fi
}

# 验证 HTTP 状态码在列表中
# 返回: 0=成功, 1=失败
assert_http_code_in() {
    local actual="$1"
    local expected_list="$2"
    local description="$3"

    local found=false
    IFS=',' read -ra codes <<< "$expected_list"
    for code in "${codes[@]}"; do
        if [[ "$actual" == "$code" ]]; then
            found=true
            break
        fi
    done

    if [[ "$found" == true ]]; then
        log_success "$description (HTTP $actual)"
        return 0
    else
        log_error "$description (期望 HTTP in [$expected_list], 实际 HTTP $actual)"
        return 1
    fi
}

# ============================================
# 日志信息函数
# ============================================

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
    echo -e "${BOLD}${YELLOW}  ▶ $1${NC}"
}
