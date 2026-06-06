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

# ========================================
# 缓存命中校验辅助函数
# ========================================

# 验证缓存命中递增和 token_ids 长度校验
# 参数: $1 - session_id
#        $2 - 期望轮次数
#        $3 - 验证模式: incremental(正常递增), nosession(恒为0), kwargs_change(第2轮失效), tools_change(第2轮失效)
verify_cache_incremental() {
    local session_id="$1"
    local expected_rounds="$2"
    local verify_mode="${3:-incremental}"

    log_step "验证缓存命中 (${verify_mode})"

    TRAJ=$(curl -s "${BASE_URL}/trajectory?session_id=${session_id}&limit=10")

    local tmpfile=$(mktemp)
    echo "$TRAJ" > "$tmpfile"

    local result=$(python3 -c "
import json, sys

with open('$tmpfile', 'r') as f:
    data = json.loads(f.read())

records = data.get('records', [])
if len(records) < $expected_rounds:
    print(f'FAIL:轨迹记录数不足, 期望>=${expected_rounds}, 实际:{len(records)}')
    sys.exit(0)

sorted_recs = sorted(records, key=lambda r: r.get('start_time', ''))

errors = []

for i, rec in enumerate(sorted_recs):
    round_num = i + 1
    cache_hit = rec.get('cache_hit_tokens', -1)
    full_conv_ids = rec.get('full_conversation_token_ids', [])
    token_ids = rec.get('token_ids', [])
    response_ids = rec.get('response_ids', [])

    # 校验2: len(full_conversation_token_ids) == len(token_ids) + len(response_ids)
    if full_conv_ids and token_ids and response_ids:
        if len(full_conv_ids) != len(token_ids) + len(response_ids):
            errors.append(f'R{round_num}校验2: full_conv={len(full_conv_ids)} != token_ids={len(token_ids)}+response_ids={len(response_ids)}={len(token_ids)+len(response_ids)}')

    if '$verify_mode' == 'nosession':
        if cache_hit != 0:
            errors.append(f'R{round_num}: cache_hit={cache_hit}, 期望=0(无session)')
    elif '$verify_mode' == 'kwargs_change':
        if round_num == 1:
            if cache_hit != 0:
                errors.append(f'R1: cache_hit={cache_hit}, 期望=0')
        else:
            if cache_hit != 0:
                errors.append(f'R{round_num}: cache_hit={cache_hit}, 期望=0(kwargs变更)')
    elif '$verify_mode' == 'tools_change':
        if round_num == 1:
            if cache_hit != 0:
                errors.append(f'R1: cache_hit={cache_hit}, 期望=0')
        else:
            if cache_hit != 0:
                errors.append(f'R{round_num}: cache_hit={cache_hit}, 期望=0(tools变更)')
    elif '$verify_mode' == 'incremental':
        if round_num == 1:
            if cache_hit != 0:
                errors.append(f'R1校验1: cache_hit={cache_hit}, 期望=0')
        else:
            prev_ids = sorted_recs[i-1].get('full_conversation_token_ids', [])
            expected = len(prev_ids)
            if cache_hit != expected:
                errors.append(f'R{round_num}校验1: cache_hit={cache_hit}, 期望={expected}')

if errors:
    print('FAIL:' + '; '.join(errors))
else:
    summary = []
    for i, rec in enumerate(sorted_recs):
        ch = rec.get('cache_hit_tokens', '?')
        fl = len(rec.get('full_conversation_token_ids', []))
        ti = len(rec.get('token_ids', []))
        ri = len(rec.get('response_ids', []))
        summary.append(f'R{i+1}:cache_hit={ch},full_conv={fl},token_ids={ti},response_ids={ri}')
    print('PASS:' + ', '.join(summary))
" 2>/dev/null)

    rm -f "$tmpfile"

    if echo "$result" | grep -q "^PASS:"; then
        log_success "$result"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_error "$result"
        TESTS_TOTAL=$((TESTS_TOTAL + 1))
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}
