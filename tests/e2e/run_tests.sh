#!/bin/bash
# E2E 测试顶层编排器
# 支持按层运行或跨层运行测试用例
#
# 用法:
#   ./run_tests.sh                          运行全部两层
#   ./run_tests.sh --layer nginx            仅运行 Nginx 层 (port 12345)
#   ./run_tests.sh --layer proxy            仅运行 Proxy 层 (port 12300)
#   ./run_tests.sh F100                     按编号搜索两层运行
#   ./run_tests.sh --layer nginx F100 F101  指定层内特定用例

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 全局计数
TOTAL_SCENARIOS=0
PASSED_SCENARIOS=0
FAILED_SCENARIOS=0

# 打印帮助
print_usage() {
    echo "用法:"
    echo "  $0                          运行全部两层测试"
    echo "  $0 --layer nginx            仅运行 Nginx 层 (port 12345)"
    echo "  $0 --layer proxy            仅运行 Proxy 层 (port 12300)"
    echo "  $0 <场景编号>                按编号搜索两层运行 (如: $0 F200)"
    echo "  $0 --layer nginx F100 F101  指定层内特定用例"
    echo ""
    echo "可用层:"
    echo "  nginx   - Nginx 入口层测试 (port 12345)"
    echo "  proxy   - 直连 Proxy 层测试 (port 12300)"
    echo "  archive - 归档调度层测试"
}

# 运行指定层
run_layer() {
    local layer_dir="$1"
    shift
    local layer_name=$(basename "$layer_dir")

    echo ""
    echo "=========================================="
    echo -e "${BLUE}启动 ${layer_name} 层测试${NC}"
    echo "=========================================="

    if bash "${layer_dir}/run_layer.sh" "$@"; then
        return 0
    else
        return 1
    fi
}

# 在所有层中搜索并运行指定编号的场景
run_scenario_cross_layer() {
    local scenario_id="$1"
    local found=0

    for layer_dir in "${SCRIPT_DIR}/layers/nginx" "${SCRIPT_DIR}/layers/proxy" "${SCRIPT_DIR}/layers/archive"; do
        local matches=("${layer_dir}/scenarios/${scenario_id}"*.sh)
        if [ -f "${matches[0]}" ]; then
            if run_layer "$layer_dir" "$scenario_id"; then
                PASSED_SCENARIOS=$((PASSED_SCENARIOS + 1))
            else
                FAILED_SCENARIOS=$((FAILED_SCENARIOS + 1))
            fi
            TOTAL_SCENARIOS=$((TOTAL_SCENARIOS + 1))
            found=1
            break
        fi
    done

    if [ $found -eq 0 ]; then
        echo -e "${RED}错误: 场景 ${scenario_id} 在所有层中均未找到${NC}"
        TOTAL_SCENARIOS=$((TOTAL_SCENARIOS + 1))
        FAILED_SCENARIOS=$((FAILED_SCENARIOS + 1))
        return 1
    fi
    return 0
}

# 打印最终摘要
print_final_summary() {
    echo ""
    echo "=========================================="
    echo "E2E 测试总结"
    echo "=========================================="
    echo -e "场景总计: ${TOTAL_SCENARIOS}"
    echo -e "场景通过: ${GREEN}${PASSED_SCENARIOS}${NC}"
    echo -e "场景失败: ${RED}${FAILED_SCENARIOS}${NC}"
    echo "=========================================="

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
    # 检查 layers 目录
    if [ ! -d "${SCRIPT_DIR}/layers" ]; then
        echo -e "${RED}错误: layers 目录不存在${NC}"
        exit 1
    fi

    LAYER=""
    SCENARIO_IDS=()

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --layer|-l)
                LAYER="$2"
                shift 2
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                SCENARIO_IDS+=("$1")
                shift
                ;;
        esac
    done

    # 根据参数决定运行策略
    if [ -n "$LAYER" ]; then
        # 指定了层
        case "$LAYER" in
            nginx|1)
                if [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
                    run_layer "${SCRIPT_DIR}/layers/nginx" "${SCENARIO_IDS[@]}"
                else
                    run_layer "${SCRIPT_DIR}/layers/nginx"
                fi
                TOTAL_SCENARIOS=$?
                ;;
            proxy|2)
                if [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
                    run_layer "${SCRIPT_DIR}/layers/proxy" "${SCENARIO_IDS[@]}"
                else
                    run_layer "${SCRIPT_DIR}/layers/proxy"
                fi
                TOTAL_SCENARIOS=$?
                ;;
            archive|3)
                if [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
                    run_layer "${SCRIPT_DIR}/layers/archive" "${SCENARIO_IDS[@]}"
                else
                    run_layer "${SCRIPT_DIR}/layers/archive"
                fi
                TOTAL_SCENARIOS=$?
                ;;
            *)
                echo -e "${RED}未知层: ${LAYER}（可用: nginx, proxy, archive）${NC}"
                exit 1
                ;;
        esac
    elif [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
        # 未指定层，但有指定场景编号 -> 跨层搜索
        for id in "${SCENARIO_IDS[@]}"; do
            run_scenario_cross_layer "$id"
        done
        print_final_summary
    else
        # 无参数 -> 运行全部三层
        echo -e "${YELLOW}运行全部三层测试...${NC}"

        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 1: Nginx Entry (port 12345)${NC}"
        echo "=========================================="
        run_layer "${SCRIPT_DIR}/layers/nginx" || true

        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 2: Direct Proxy (port 12300)${NC}"
        echo "=========================================="
        run_layer "${SCRIPT_DIR}/layers/proxy" || true

        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 3: Archive Scheduler${NC}"
        echo "=========================================="
        run_layer "${SCRIPT_DIR}/layers/archive" || true

        # 汇总（简单方式：依赖子进程的退出码输出）
        echo ""
        echo -e "${GREEN}全部层测试执行完毕${NC}"
    fi
}

main "$@"
