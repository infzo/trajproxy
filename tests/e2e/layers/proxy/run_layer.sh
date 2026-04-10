#!/bin/bash
# Layer 2 测试运行器: 直连 Proxy (port 12300)
# 用法:
#   ./run_layer.sh           # 运行本层所有测试
#   ./run_layer.sh F200      # 运行指定场景
#   ./run_layer.sh F200 F201 # 运行多个场景

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS_DIR="${SCRIPT_DIR}/scenarios"

LAYER_NAME="Layer 2 - Direct Proxy (port 12300)"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 全局测试计数
TOTAL_SCENARIOS=0
PASSED_SCENARIOS=0
FAILED_SCENARIOS=0

# 打印帮助
print_usage() {
    echo "用法:"
    echo "  $0             运行本层所有测试场景"
    echo "  $0 <场景编号>  运行指定场景（如: $0 F200）"
    echo "  $0 F200 F201   运行多个场景"
    echo ""
    echo "当前层: ${LAYER_NAME}"
    echo ""
    echo "可用的测试场景:"
    for f in "${SCENARIOS_DIR}"/*.sh; do
        if [ -f "$f" ]; then
            filename=$(basename "$f" .sh)
            echo "  - ${filename}"
        fi
    done
}

# 运行单个测试场景
run_scenario() {
    local scenario_id="$1"
    local scenario_file="${SCENARIOS_DIR}/${scenario_id}*.sh"

    # 查找匹配的场景文件
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

    # 运行测试脚本
    if bash "$scenario_path"; then
        PASSED_SCENARIOS=$((PASSED_SCENARIOS + 1))
        return 0
    else
        FAILED_SCENARIOS=$((FAILED_SCENARIOS + 1))
        return 1
    fi
}

# 运行所有测试场景
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

# 打印最终摘要
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

# 主入口
main() {
    # 检查 scenarios 目录
    if [ ! -d "$SCENARIOS_DIR" ]; then
        echo -e "${RED}错误: scenarios 目录不存在${NC}"
        exit 1
    fi

    # 根据参数运行测试
    if [ $# -eq 0 ]; then
        # 运行所有测试
        run_all_scenarios
    elif [ "$1" == "-h" ] || [ "$1" == "--help" ]; then
        # 显示帮助
        print_usage
        exit 0
    else
        # 运行指定的测试场景
        for scenario_id in "$@"; do
            run_scenario "$scenario_id"
        done
    fi

    print_final_summary
}

main "$@"
