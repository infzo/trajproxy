#!/bin/bash
# Comparison Layer 测试运行器
# 用法:
#   ./run_layer.sh           # 运行本层所有测试
#   ./run_layer.sh C101      # 运行指定场景

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS_DIR="${SCRIPT_DIR}/scenarios"
LAYER_NAME="Comparison Layer"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TOTAL_SCENARIOS=0
PASSED_SCENARIOS=0
FAILED_SCENARIOS=0

print_usage() {
    echo "用法:"
    echo "  $0             运行本层所有测试场景"
    echo "  $0 <场景编号>  运行指定场景（如: $0 C101）"
    echo ""
    echo "可用的测试场景:"
    for f in "${SCENARIOS_DIR}"/*.sh; do
        if [ -f "$f" ]; then
            filename=$(basename "$f" .sh)
            echo "  - ${filename}"
        fi
    done
}

run_scenario() {
    local scenario_id="$1"
    local scenario_file="${SCENARIOS_DIR}/${scenario_id}_"*".sh"
    local matched_files=( $scenario_file )

    if [ ${#matched_files[@]} -eq 0 ] || [ ! -f "${matched_files[0]}" ]; then
        echo -e "${RED}错误: 找不到场景 ${scenario_id}${NC}"
        return 1
    fi

    local scenario_path="${matched_files[0]}"
    local scenario_name=$(basename "$scenario_path" .sh)

    echo ""
    echo "========================================"
    echo -e "${BLUE}[${LAYER_NAME}] 运行场景: ${scenario_name}${NC}"
    echo "========================================"

    TOTAL_SCENARIOS=$((TOTAL_SCENARIOS + 1))
    export FAILURE_CONTEXT="${scenario_name}"
    local start_ts=$(date +%s)
    if bash "$scenario_path"; then
        local end_ts=$(date +%s)
        local duration=$((end_ts - start_ts))
        echo -e "${GREEN}场景 ${scenario_name} 耗时: ${duration}s${NC}"
        PASSED_SCENARIOS=$((PASSED_SCENARIOS + 1))
        # 写入计时日志（供顶层 run_tests.sh 汇总）
        if [ -n "${TIMING_LOG:-}" ] && [ -w "${TIMING_LOG:-}" ]; then
            echo "${scenario_name}|${duration}" >> "$TIMING_LOG"
        fi
        return 0
    else
        local end_ts=$(date +%s)
        local duration=$((end_ts - start_ts))
        echo -e "${RED}场景 ${scenario_name} 耗时: ${duration}s (失败)${NC}"
        FAILED_SCENARIOS=$((FAILED_SCENARIOS + 1))
        if [ -n "${TIMING_LOG:-}" ] && [ -w "${TIMING_LOG:-}" ]; then
            echo "${scenario_name}|${duration}" >> "$TIMING_LOG"
        fi
        return 1
    fi
}

run_all_scenarios() {
    echo -e "${YELLOW}[${LAYER_NAME}] 运行所有测试场景...${NC}"
    for f in "${SCENARIOS_DIR}"/*.sh; do
        if [ -f "$f" ]; then
            local scenario_name=$(basename "$f" .sh)
            local scenario_id=$(echo "$scenario_name" | cut -d'_' -f1)
            run_scenario "$scenario_id"
        fi
    done
}

print_final_summary() {
    echo ""
    echo "========================================"
    echo "[${LAYER_NAME}] 测试总结"
    echo "========================================"
    echo -e "场景总计: ${TOTAL_SCENARIOS}"
    echo -e "场景通过: ${GREEN}${PASSED_SCENARIOS}${NC}"
    echo -e "场景失败: ${RED}${FAILED_SCENARIOS}${NC}"
    echo "========================================"
    if [ $FAILED_SCENARIOS -eq 0 ]; then
        echo -e "${GREEN}所有测试通过！${NC}"
        exit 0
    else
        echo -e "${RED}存在失败的测试！${NC}"
        exit 1
    fi
}

main() {
    if [ ! -d "$SCENARIOS_DIR" ]; then
        echo -e "${RED}错误: scenarios 目录不存在${NC}"
        exit 1
    fi

    source "${SCRIPT_DIR}/utils.sh"
    echo -e "${YELLOW}前置检查: 确认服务可用...${NC}"
    if ! check_vllm_health; then
        echo -e "${RED}前置检查失败: vLLM 服务不可用${NC}"
        exit 1
    fi
    if ! check_proxy_health; then
        echo -e "${RED}前置检查失败: trajproxy 服务不可用${NC}"
        exit 1
    fi
    echo -e "${GREEN}前置检查通过${NC}"
    echo ""

    if [ $# -eq 0 ]; then
        run_all_scenarios
    elif [ "$1" == "-h" ] || [ "$1" == "--help" ]; then
        print_usage
        exit 0
    else
        for scenario_id in "$@"; do
            run_scenario "$scenario_id"
        done
    fi

    print_final_summary
}

main "$@"
