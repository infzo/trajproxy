#!/bin/bash
# E2E 测试顶层编排器
# 支持按层运行或跨层运行测试用例
#
# 用法:
#   ./run_tests.sh                          运行全部四层（默认不含 P*/A* 场景）
#   ./run_tests.sh --all                    运行全部四层（包含 P*/A* 场景）
#   ./run_tests.sh --layer nginx            仅运行 Nginx 层 (port 12345)
#   ./run_tests.sh --layer proxy            仅运行 Proxy 层 (port 12300)
#   ./run_tests.sh F100                     按编号搜索两层运行
#   ./run_tests.sh --layer nginx F100 F101  指定层内特定用例
#
# 默认行为:
#   - 直接访问真实推理服务
#   - 跳过性能测试（P 开头）和归档测试（A 开头）用例
#   - --all 运行全部用例（含 P*/A*）
#   - 显式指定场景编号时不过滤（如 ./run_tests.sh P100 会正常执行）

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

# 计时日志文件（由 run_tests.sh 创建，传递给 run_layer.sh 使用）
TIMING_LOG=""

# 默认跳过的场景前缀（P=性能测试, A=归档测试），显式指定场景编号时不过滤
# 可通过 --all 清除此过滤，或通过 SKIP_PREFIXES 环境变量自定义
SKIP_PREFIXES="${SKIP_PREFIXES:-P,A}"

# 打印帮助
print_usage() {
    echo "用法:"
    echo "  $0                          运行全部四层（默认跳过 P*/A* 场景）"
    echo "  $0 --all                    运行全部四层（包含 P*/A* 所有场景）"
    echo "  $0 --layer nginx            仅运行 Nginx 层 (port 12345)"
    echo "  $0 --layer proxy            仅运行 Proxy 层 (port 12300)"
    echo "  $0 --layer archive          仅运行 Archive 层"
    echo "  $0 --layer comparison       仅运行 Comparison 层"
    echo "  $0 <场景编号>                按编号搜索各层运行 (如: $0 F200)"
    echo "  $0 --layer nginx F100 F101  指定层内特定用例"
    echo ""
    echo "场景过滤:"
    echo "  --all                       运行全部场景（含 P*/A*，默认跳过性能和归档用例）"
    echo "  SKIP_PREFIXES=P,A           自定义跳过前缀（逗号分隔，默认跳过 P 和 A）"
    echo "  SKIP_PREFIXES=''            清空前缀过滤，等同 --all"
    echo ""
    echo "可用层:"
    echo "  nginx      - Nginx 入口层测试 (port 12345)"
    echo "  proxy      - 直连 Proxy 层测试 (port 12300)"
    echo "  archive    - 归档调度层测试"
    echo "  comparison - 对比测试层 (vLLM:8000 vs Proxy:12300)"
}

# 获取指定层过滤后的场景 ID 列表
# 参数: $1 = layer 目录路径
# 根据 SKIP_PREFIXES 环境变量过滤以指定前缀开头的场景
get_filtered_scenarios() {
    local layer_dir="$1"
    local scenarios=()
    local skipped=()
    IFS=',' read -ra prefixes <<< "${SKIP_PREFIXES:-}"
    for f in "${layer_dir}/scenarios/"*.sh; do
        if [ ! -f "$f" ]; then
            continue
        fi
        local name=$(basename "$f" .sh)
        local id=$(echo "$name" | cut -d'_' -f1)
        # 检查是否匹配跳过前缀
        local should_skip=false
        for prefix in "${prefixes[@]}"; do
            prefix=$(echo "$prefix" | xargs)  # trim whitespace
            if [ -n "$prefix" ] && [[ "$id" == "$prefix"* ]]; then
                should_skip=true
                break
            fi
        done
        if $should_skip; then
            skipped+=("$name")
        else
            scenarios+=("$id")
        fi
    done
    # 输出跳过的场景到 stderr 以便 stdout 只输出场景 ID
    if [ ${#skipped[@]} -gt 0 ]; then
        echo -e "${YELLOW}[FILTER] 跳过场景: ${skipped[*]}${NC}" >&2
    fi
    echo "${scenarios[@]}"
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

    for layer_dir in "${SCRIPT_DIR}/layers/nginx" "${SCRIPT_DIR}/layers/proxy" "${SCRIPT_DIR}/layers/archive" "${SCRIPT_DIR}/layers/comparison"; do
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

# 打印计时统计报告：耗时最长的前10个用例 + 全部用例平均耗时
print_timing_report() {
    if [ -z "${TIMING_LOG:-}" ] || [ ! -f "$TIMING_LOG" ] || [ ! -s "$TIMING_LOG" ]; then
        return
    fi

    echo ""
    echo "=========================================="
    echo "耗时统计报告"
    echo "=========================================="

    local total_cases=0
    local total_time=0

    while IFS='|' read -r name duration; do
        total_cases=$((total_cases + 1))
        total_time=$((total_time + duration))
    done < "$TIMING_LOG"

    # 前 10 个最慢用例
    echo -e "${YELLOW}耗时最长的前 10 个用例:${NC}"
    echo "──────────────────────────────────────────"
    printf "  %-6s  %-40s  %s\n" "排名" "用例名称" "耗时"
    echo "──────────────────────────────────────────"
    sort -t'|' -k2 -rn "$TIMING_LOG" | head -n 10 | {
        rank=0
        while IFS='|' read -r name duration; do
            rank=$((rank + 1))
            printf "  %-6s  %-40s  %s\n" "#${rank}" "$name" "${duration}s"
        done
    }
    echo "──────────────────────────────────────────"

    # 平均耗时
    if [ $total_cases -gt 0 ]; then
        local avg=$((total_time / total_cases))
        echo -e "全部用例总数: ${total_cases}  |  总耗时: ${total_time}s  |  ${YELLOW}平均耗时: ${avg}s${NC}"
    fi
    echo "=========================================="
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

    # 输出耗时报告
    print_timing_report

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

    # 创建计时日志临时文件，供各 run_layer.sh 写入
    TIMING_LOG=$(mktemp /tmp/e2e_timing_XXXXXX.log)
    export TIMING_LOG
    trap "rm -f '$TIMING_LOG'" EXIT

    LAYER=""
    SCENARIO_IDS=()

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --layer|-l)
                LAYER="$2"
                shift 2
                ;;
            --all)
                SKIP_PREFIXES=""
                shift
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

    # ========================================
    # 运行测试
    # ========================================

    # ========================================
    # 辅助函数: 运行指定层（自动应用场景过滤）
    # ========================================
    run_layer_with_filter() {
        local layer_dir="$1"
        local layer_name=$(basename "$layer_dir")

        if [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
            # 用户显式指定了场景编号，不过滤
            run_layer "$layer_dir" "${SCENARIO_IDS[@]}"
        else
            # 获取过滤后的场景列表
            local filtered_ids=($(get_filtered_scenarios "$layer_dir"))
            if [ ${#filtered_ids[@]} -eq 0 ]; then
                echo -e "${YELLOW}[${layer_name}] 无匹配场景（全部被过滤），跳过${NC}"
                return 0
            fi
            run_layer "$layer_dir" "${filtered_ids[@]}"
        fi
    }

    if [ -n "$LAYER" ]; then
        # 指定了层
        case "$LAYER" in
            nginx|1)
                run_layer_with_filter "${SCRIPT_DIR}/layers/nginx"
                TOTAL_SCENARIOS=$?
                ;;
            proxy|2)
                run_layer_with_filter "${SCRIPT_DIR}/layers/proxy"
                TOTAL_SCENARIOS=$?
                ;;
            archive|3)
                run_layer_with_filter "${SCRIPT_DIR}/layers/archive"
                TOTAL_SCENARIOS=$?
                ;;
            comparison|4)
                run_layer_with_filter "${SCRIPT_DIR}/layers/comparison"
                TOTAL_SCENARIOS=$?
                ;;
            *)
                echo -e "${RED}未知层: ${LAYER}（可用: nginx, proxy, archive, comparison）${NC}"
                exit 1
                ;;
        esac
        # 单层层模式也输出耗时报告
        print_timing_report
    elif [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
        # 未指定层，但有指定场景编号 -> 跨层搜索（显式指定，不过滤）
        for id in "${SCENARIO_IDS[@]}"; do
            run_scenario_cross_layer "$id"
        done
        print_final_summary
    else
        # 无参数 -> 运行全部四层（默认过滤 P*/A*）
        if [ -n "${SKIP_PREFIXES:-}" ]; then
            echo -e "${YELLOW}运行全部四层测试（跳过前缀 [${SKIP_PREFIXES}] 的场景）...${NC}"
        else
            echo -e "${YELLOW}运行全部四层测试（包含全部场景）...${NC}"
        fi

        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 1: Nginx Entry (port 12345)${NC}"
        echo "=========================================="
        run_layer_with_filter "${SCRIPT_DIR}/layers/nginx" || true

        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 2: Direct Proxy (port 12300)${NC}"
        echo "=========================================="
        run_layer_with_filter "${SCRIPT_DIR}/layers/proxy" || true

        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 3: Archive Scheduler${NC}"
        echo "=========================================="
        run_layer_with_filter "${SCRIPT_DIR}/layers/archive" || true

        echo ""
        echo -e "${BLUE}Layer 4: Comparison (vLLM:8000 vs Proxy:12300)${NC}"
        echo "=========================================="
        run_layer_with_filter "${SCRIPT_DIR}/layers/comparison" || true

        # 汇总
        echo ""
        echo -e "${GREEN}全部层测试执行完毕${NC}"
        print_timing_report
    fi
}

main "$@"
