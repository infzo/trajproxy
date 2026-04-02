#!/bin/bash
# ============================================
# TrajProxy 验证测试 - 一键执行脚本
# ============================================
# 用途: 运行所有测试场景
# 使用: ./run_all.sh [场景编号]
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
source "${SCRIPT_DIR}/config.sh"

# ============================================
# 全局统计
# ============================================
TOTAL_SCENARIOS=0
PASSED_SCENARIOS=0
FAILED_SCENARIOS=0

# ============================================
# 帮助信息
# ============================================
show_help() {
    cat << EOF
TrajProxy 验证测试套件

用法: $0 [选项] [场景编号]

场景编号:
  1     场景一：模型注册、罗列、删除接口测试
  2     场景二：OpenAI Chat 测试
  3     场景三：Claude Chat 测试
  4     场景四：Tool / Reason 测试
  all   运行所有场景（默认）

选项:
  --proxy-url URL       TrajProxy 地址 (默认: http://localhost:12300)
  --nginx-url URL       Nginx 网关地址 (默认: http://localhost:12345)
  --inference-url URL   推理服务地址 (默认: http://localhost:8000/v1)
  --model NAME          测试模型名称
  --verbose, -v         显示详细输出
  --quiet, -q           简洁输出
  --help, -h            显示此帮助信息

环境变量:
  PROXY_URL             TrajProxy 地址
  NGINX_URL             Nginx 网关地址
  INFERENCE_URL         推理服务地址
  API_KEY               API 密钥
  TEST_MODEL            测试模型名称
  VERBOSE               显示详细输出

示例:
  # 运行所有测试
  $0

  # 只运行场景一
  $0 1

  # 自定义配置运行
  PROXY_URL=http://192.168.1.100:12300 $0

  # 运行多个场景
  $0 1 2 3

EOF
    exit 0
}

# ============================================
# 运行单个场景
# ============================================
run_scenario() {
    local scenario_num="$1"
    local script_name=""
    local scenario_name=""

    case "$scenario_num" in
        1)
            script_name="scenario_1_model_mgmt.sh"
            scenario_name="场景一：模型注册、罗列、删除接口测试"
            ;;
        2)
            script_name="scenario_2_openai_chat.sh"
            scenario_name="场景二：OpenAI Chat 测试"
            ;;
        3)
            script_name="scenario_3_claude_chat.sh"
            scenario_name="场景三：Claude Chat 测试"
            ;;
        4)
            script_name="scenario_4_tool_reason.sh"
            scenario_name="场景四：Tool / Reason 测试"
            ;;
        *)
            log_error "未知场景编号: $scenario_num"
            return 1
            ;;
    esac

    local script_path="${SCRIPT_DIR}/${script_name}"

    if [[ ! -f "$script_path" ]]; then
        log_error "场景脚本不存在: $script_path"
        return 1
    fi

    TOTAL_SCENARIOS=$((TOTAL_SCENARIOS + 1))

    print_header "$scenario_name"

    if bash "$script_path"; then
        PASSED_SCENARIOS=$((PASSED_SCENARIOS + 1))
        echo ""
        log_info "场景 $scenario_num 通过 ✓"
        return 0
    else
        FAILED_SCENARIOS=$((FAILED_SCENARIOS + 1))
        echo ""
        log_error "场景 $scenario_num 失败 ✗"
        return 1
    fi
}

# ============================================
# 参数解析
# ============================================
SCENARIOS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --proxy-url)
            export PROXY_URL="$2"
            shift 2
            ;;
        --nginx-url)
            export NGINX_URL="$2"
            shift 2
            ;;
        --inference-url)
            export INFERENCE_URL="$2"
            shift 2
            ;;
        --model)
            export TEST_MODEL="$2"
            shift 2
            ;;
        --verbose|-v)
            export VERBOSE=true
            shift
            ;;
        --quiet|-q)
            export VERBOSE=false
            shift
            ;;
        --help|-h)
            show_help
            ;;
        all)
            SCENARIOS=(1 2 3 4)
            shift
            ;;
        [1-4])
            SCENARIOS+=("$1")
            shift
            ;;
        *)
            echo "未知参数: $1"
            show_help
            ;;
    esac
done

# 如果没有指定场景，运行所有场景
if [[ ${#SCENARIOS[@]} -eq 0 ]]; then
    SCENARIOS=(1 2 3 4)
fi

# ============================================
# 主程序
# ============================================
main() {
    print_header "TrajProxy 验证测试套件"

    # 显示配置信息
    echo -e "${CYAN}当前测试配置:${NC}"
    echo -e "  PROXY_URL:         ${PROXY_URL}"
    echo -e "  NGINX_URL:         ${NGINX_URL}"
    echo -e "  INFERENCE_URL:     ${INFERENCE_URL}"
    echo -e "  API_KEY:           ${API_KEY}"
    echo -e "  TEST_MODEL:        ${TEST_MODEL}"
    echo -e "  TOKENIZER_PATH:    ${TOKENIZER_PATH}"
    echo -e "  TEST_RUN_ID:       ${TEST_RUN_ID}"
    echo ""

    # 检查依赖
    check_dependencies

    # 检查服务健康状态
    log_info "检查服务健康状态..."
    local health_response
    health_response=$(curl -s -w "\n%{http_code}" "${PROXY_URL}/health" 2>/dev/null || echo -e "\n000")
    local health_code=$(http_code "$health_response")

    if [[ "$health_code" != "200" ]]; then
        log_error "TrajProxy 服务不可用 (HTTP $health_code)"
        log_info "请确保服务正在运行: ${PROXY_URL}"
        exit 1
    fi
    log_success "TrajProxy 服务正常"

    # 运行测试场景
    local failed=false
    for scenario in "${SCENARIOS[@]}"; do
        if ! run_scenario "$scenario"; then
            failed=true
        fi
    done

    # 打印总摘要
    echo ""
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo -e "${BOLD}${CYAN}  总测试摘要${NC}"
    echo -e "${BOLD}${CYAN}========================================${NC}"
    echo ""
    echo -e "  总场景数:  ${BOLD}${TOTAL_SCENARIOS}${NC}"
    echo -e "  ${GREEN}通过场景: ${PASSED_SCENARIOS}${NC}"
    echo -e "  ${RED}失败场景: ${FAILED_SCENARIOS}${NC}"
    echo ""

    if [[ "$failed" == true ]]; then
        echo -e "${RED}${BOLD}✗ 部分场景测试失败${NC}"
        exit 1
    else
        echo -e "${GREEN}${BOLD}✓ 所有场景测试通过！${NC}"
        exit 0
    fi
}

# 运行主程序
main
