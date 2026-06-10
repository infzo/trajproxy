#!/bin/bash
# Comparison Layer 测试运行器
# 用法:
#   ./run_layer.sh           # 运行本层所有测试
#   ./run_layer.sh C101      # 运行指定场景
#
# 环境变量:
#   E2E_JOBS    - Layer 内并发执行数（默认 1，顺序执行）
#   E2E_LOG_DIR - 日志落盘目录（由 run_tests.sh 创建，单独运行时按需自动创建）

# 注意：使用 LAYER_DIR 而非 SCRIPT_DIR，避免被 utils.sh 的同名全局变量覆盖
LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS_DIR="${LAYER_DIR}/scenarios"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

LAYER_NAME="comparison"

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

main() {
    if [ ! -d "$SCENARIOS_DIR" ]; then
        echo -e "${RED}错误: scenarios 目录不存在${NC}"
        exit 1
    fi

    # 加载公共工具（utils.sh 会引入 config.sh、check_*_health 和并发函数）
    # 注意：source 后 LAYER_DIR 仍保留原值，因为 utils.sh 只覆盖 SCRIPT_DIR
    source "${LAYER_DIR}/../../utils.sh"
    source "${LAYER_DIR}/utils.sh"

    if [ "$1" == "-h" ] || [ "$1" == "--help" ]; then
        print_usage
        exit 0
    fi

    # 前置检查：确认双端服务可用
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

    # 委托给公共并发执行函数
    scenario_ids=()
    if [ $# -gt 0 ]; then
        scenario_ids=("$@")
    fi
    run_scenarios_parallel "$LAYER_NAME" "$LAYER_DIR" "${scenario_ids[@]}"
}

main "$@"
