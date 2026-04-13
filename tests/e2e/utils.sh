#!/bin/bash
# 公共工具函数

# 导入配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

# 测试计数器
TESTS_TOTAL=0
TESTS_PASSED=0
TESTS_FAILED=0

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_step() {
    echo -e "${YELLOW}[STEP]${NC} $1"
}

# 断言函数：相等
assert_eq() {
    local expected="$1"
    local actual="$2"
    local message="$3"

    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    if [ "$expected" == "$actual" ]; then
        log_success "$message"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        log_error "$message"
        echo "    期望: $expected"
        echo "    实际: $actual"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# 断言函数：包含
assert_contains() {
    local haystack="$1"
    local needle="$2"
    local message="$3"

    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    if [[ "$haystack" == *"$needle"* ]]; then
        log_success "$message"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        log_error "$message"
        echo "    未找到: $needle"
        echo "    内容: $haystack"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# 断言函数：不包含
assert_not_contains() {
    local haystack="$1"
    local needle="$2"
    local message="$3"

    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    if [[ "$haystack" != *"$needle"* ]]; then
        log_success "$message"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        log_error "$message"
        echo "    不应包含: $needle"
        echo "    内容: $haystack"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# 断言函数：HTTP 状态码
assert_http_status() {
    local expected="$1"
    local actual="$2"
    local message="$3"

    assert_eq "$expected" "$actual" "$message"
}

# 打印 curl 命令（格式化输出）
log_curl_cmd() {
    echo -e "${BLUE}[CMD]${NC}"
    echo "$1" | fold -s -w 100
}

# 打印响应结果（格式化 JSON，确保 Unicode 正确显示）
log_response() {
    if [[ "$1" == "HTTP Status:"* ]]; then
        echo -e "${BLUE}[RES]${NC} $1"
    else
        echo -e "${BLUE}[RES]${NC}"
        echo "$1" | python3 -c "
import sys
import json
data = sys.stdin.read()
try:
    obj = json.loads(data)
    print(json.dumps(obj, indent=4, ensure_ascii=False))
except:
    print(data)
"
    fi
}

# 分离响应体和状态码（兼容 macOS）
split_response() {
    local response="$1"
    # 使用 sed 删除最后一行获取 body，获取最后一行获取状态码
    RESPONSE_BODY=$(echo "$response" | sed '$d')
    RESPONSE_STATUS=$(echo "$response" | sed -n '$p')
}

# 打印分隔线
log_separator() {
    echo "----------------------------------------"
}

# 提取 JSON 字段值（使用 grep 和 sed，不依赖 jq）
json_get() {
    local json="$1"
    local key="$2"

    # 使用 grep 和 sed 提取值
    echo "$json" | grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | sed "s/\"$key\"[[:space:]]*:[[:space:]]*\"//" | sed 's/"$//' | head -1
}

# 提取 JSON 字段值（数字类型）
json_get_number() {
    local json="$1"
    local key="$2"

    echo "$json" | grep -o "\"$key\"[[:space:]]*:[[:space:]]*[0-9]*" | sed "s/\"$key\"[[:space:]]*:[[:space:]]*//" | head -1
}

# 提取 JSON 字段值（布尔类型）
json_get_bool() {
    local json="$1"
    local key="$2"

    echo "$json" | grep -o "\"$key\"[[:space:]]*:[[:space:]]*[a-z]*" | sed "s/\"$key\"[[:space:]]*:[[:space:]]*//" | head -1
}

# 打印测试摘要
print_summary() {
    echo ""
    echo "========================================"
    echo "测试摘要"
    echo "========================================"
    echo -e "总计: ${TESTS_TOTAL}"
    echo -e "通过: ${GREEN}${TESTS_PASSED}${NC}"
    echo -e "失败: ${RED}${TESTS_FAILED}${NC}"
    echo "========================================"

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "${GREEN}所有测试通过！${NC}"
        return 0
    else
        echo -e "${RED}存在失败的测试！${NC}"
        return 1
    fi
}
