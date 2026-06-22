#!/bin/bash
# E2E 测试顶层编排器
# 支持按层运行或跨层运行测试用例
#
# 用法:
#   ./run_tests.sh                          运行全部五层（默认不含 T*/A* 场景）
#   ./run_tests.sh --all                    运行全部五层（包含 T*/A* 场景）
#   ./run_tests.sh --layer nginx            仅运行 Nginx 层 (port 12345)
#   ./run_tests.sh --layer proxy            仅运行 Proxy 层 (port 12300)
#   ./run_tests.sh P101                     按编号搜索各层运行
#   ./run_tests.sh --layer nginx N101 N102  指定层内特定用例
#
# 默认行为:
#   - 直接访问真实推理服务
#   - 跳过性能测试（T 开头）、归档测试（A 开头）和精简存储模式测试（S 开头）用例
#   - --all 运行全部用例（含 T*/A*/S*）
#   - 显式指定场景编号时不过滤（如 ./run_tests.sh T101 会正常执行）

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
FAILURE_LOG=""

# 默认跳过的场景前缀（T=性能测试, A=归档测试, S=精简存储模式测试），显式指定场景编号时不过滤
# 可通过 --all 清除此过滤，或通过 --skip / --only / SKIP_PREFIXES 环境变量自定义
# 注意：S 系列需要服务以 storage_mode=compact 部署，与全量模式（默认）互斥，不能同环境运行
SKIP_PREFIXES="${SKIP_PREFIXES:-T,A,S}"
ONLY_PREFIXES=""

# 打印帮助
print_usage() {
    echo "用法:"
    echo "  $0                          运行全部五层（默认跳过 T*/A*/S* 场景）"
    echo "  $0 --all                    运行全部五层（包含 T*/A*/S* 所有场景）"
    echo "  $0 --layer nginx            仅运行 Nginx 层 (port 12345)"
    echo "  $0 --layer proxy            仅运行 Proxy 层 (port 12300)"
    echo "  $0 --layer archive          仅运行 Archive 层"
    echo "  $0 --layer comparison       仅运行 Comparison 层"
    echo "  $0 --layer performance      仅运行 Performance 层"
    echo "  $0 <场景编号>                按编号搜索各层运行 (如: $0 P101)"
    echo "  $0 --layer nginx N101 N102  指定层内特定用例"
    echo ""
    echo "场景过滤:"
    echo "  --all                       运行全部场景（含 T*/A*，默认跳过性能和归档用例）"
    echo "  --only, -o <前缀>           只运行指定前缀的场景，可多次使用（如: -o N -o P）"
    echo "  --skip, -s <前缀>           自定义跳过前缀，逗号分隔（如: -s C,A）"
    echo "  --only 与 --skip 互斥，不可同时使用"
    echo ""
    echo "并发控制:"
    echo "  --jobs, -j <N>              Layer 内并发执行场景数（默认 1，顺序执行）"
    echo "  环境变量 E2E_JOBS=<N>       等价于 -j N，命令行 -j 优先"
    echo "  并发模式下日志实时输出到终端，同时落盘到有序日志文件（按场景编号排序）"
    echo ""
    echo "  环境变量: SKIP_PREFIXES=T,A  （兼容旧用法，等同 --skip T,A）"
    echo ""
    echo "可用层:"
    echo "  nginx      - Nginx 入口层测试 (port 12345)"
    echo "  proxy      - 直连 Proxy 层测试 (port 12300)"
    echo "  archive    - 归档调度层测试"
    echo "  comparison - 对比测试层 (vLLM:8000 vs Proxy:12300)"
    echo "  performance - 性能压力测试层 (port 12345)"
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

    for layer_dir in "${SCRIPT_DIR}/layers/nginx" "${SCRIPT_DIR}/layers/proxy" "${SCRIPT_DIR}/layers/archive" "${SCRIPT_DIR}/layers/comparison" "${SCRIPT_DIR}/layers/performance"; do
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

# 打印失败结果报告：汇总所有失败用例及原因
print_failure_report() {
    if [ -z "${FAILURE_LOG:-}" ] || [ ! -f "$FAILURE_LOG" ] || [ ! -s "$FAILURE_LOG" ]; then
        return
    fi

    echo ""
    echo "=========================================="
    echo -e "${RED}测试结果报告${NC}"
    echo "=========================================="

    local fail_count=0
    local prev_scenario=""
    local index=0

    while IFS='|' read -r scenario message detail; do
        if [ "$scenario" != "$prev_scenario" ]; then
            index=$((index + 1))
            echo ""
            echo -e "  ${RED}#${index}  ${scenario}${NC}"
            prev_scenario="$scenario"
            fail_count=$((fail_count + 1))
        fi
        echo "      FAIL: ${message}"
        [ -n "$detail" ] && echo "      ${detail}"
    done < "$FAILURE_LOG"

    echo ""
    echo "──────────────────────────────────────────"
    echo -e "  失败用例: ${RED}${fail_count} 个${NC}"
    echo "=========================================="
}

# 将最终报告（耗时统计 + 失败详情 + 总结）落盘到 summary.log
# 去除 ANSI 颜色码，方便离线查阅
save_summary_log() {
    if [ -z "${E2E_LOG_DIR:-}" ] || [ ! -d "${E2E_LOG_DIR}" ]; then
        return
    fi
    local summary_file="${E2E_LOG_DIR}/summary.log"

    {
        echo "=============================="
        echo "E2E 测试最终报告"
        echo "生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "=============================="

        echo ""
        echo "=========================================="
        echo "E2E 测试总结"
        echo "=========================================="
        echo "场景总计: ${TOTAL_SCENARIOS}"
        echo "场景通过: ${PASSED_SCENARIOS}"
        echo "场景失败: ${FAILED_SCENARIOS}"
        echo "=========================================="

        # 耗时统计
        print_timing_report

        # 失败详情
        print_failure_report

        if [ ${FAILED_SCENARIOS} -eq 0 ]; then
            echo ""
            echo "结果: 所有测试通过！"
        else
            echo ""
            echo "结果: 存在失败的测试！"
        fi

        echo ""
        echo "日志目录: ${E2E_LOG_DIR}"
    } | sed $'s/\x1b\\[[0-9;]*[a-zA-Z]//g' > "$summary_file"

    echo -e "${BLUE}报告已保存: ${summary_file}${NC}"
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
    # 输出失败结果报告
    print_failure_report

    # 将报告落盘到 summary.log
    save_summary_log

    # 打印日志落盘目录
    if [ -n "${E2E_LOG_DIR:-}" ] && [ -d "${E2E_LOG_DIR}" ]; then
        echo -e "${BLUE}日志目录: ${E2E_LOG_DIR}${NC}"
    fi

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
    FAILURE_LOG=$(mktemp /tmp/e2e_failure_XXXXXX.log)
    export TIMING_LOG FAILURE_LOG
    trap "rm -f '$TIMING_LOG' '$FAILURE_LOG'" EXIT

    # 并发度：默认 1（串行，向后兼容）；-j 覆盖环境变量
    E2E_JOBS="${E2E_JOBS:-1}"
    export E2E_JOBS

    # 创建日志落盘目录（并发模式下各 layer 子目录会创建其下）
    # 路径: <项目根>/logs/tests/YYYYMMDD_HHMMSS/，按测试启动时间命名
    E2E_LOG_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)/logs/tests/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$E2E_LOG_DIR"
    export E2E_LOG_DIR

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
            --skip|-s)
                SKIP_PREFIXES="$2"
                shift 2
                ;;
            --only|-o)
                ONLY_PREFIXES="${ONLY_PREFIXES:+$ONLY_PREFIXES,}$2"
                shift 2
                ;;
            --jobs|-j)
                E2E_JOBS="$2"
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

    # --only 和 --skip 互斥校验
    if [ -n "$ONLY_PREFIXES" ] && [ "${SKIP_PREFIXES}" != "T,A" ]; then
        echo -e "${RED}错误: --only 和 --skip 不能同时使用（含 SKIP_PREFIXES 环境变量）${NC}" >&2
        exit 1
    fi

    # --only 转 SKIP_PREFIXES：扫描所有层场景目录，不在 only 列表中的前缀全部跳过
    if [ -n "$ONLY_PREFIXES" ]; then
        IFS=',' read -ra only_arr <<< "${ONLY_PREFIXES}"
        skip_list=()
        for prefix in $(find "${SCRIPT_DIR}/layers" -path "*/scenarios/*.sh" \
            -exec basename {} .sh \; | cut -d_ -f1 | sed 's/[0-9]*$//' | sort -u); do
            found=false
            for o in "${only_arr[@]}"; do
                [ "$prefix" = "$o" ] && found=true && break
            done
            $found || skip_list+=("$prefix")
        done
        SKIP_PREFIXES=$(IFS=','; echo "${skip_list[*]}")
    fi

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
            performance|5)
                run_layer_with_filter "${SCRIPT_DIR}/layers/performance"
                TOTAL_SCENARIOS=$?
                ;;
            *)
                echo -e "${RED}未知层: ${LAYER}（可用: nginx, proxy, archive, comparison, performance）${NC}"
                exit 1
                ;;
        esac
        # 单层层模式也输出耗时报告
        print_timing_report
        # 单层层模式也输出失败结果报告
        print_failure_report
        # 将报告落盘到 summary.log
        save_summary_log
        # 打印日志落盘目录
        if [ -n "${E2E_LOG_DIR:-}" ] && [ -d "${E2E_LOG_DIR}" ]; then
            echo -e "${BLUE}日志目录: ${E2E_LOG_DIR}${NC}"
        fi
    elif [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
        # 未指定层，但有指定场景编号 -> 跨层搜索（显式指定，不过滤）
        for id in "${SCENARIO_IDS[@]}"; do
            run_scenario_cross_layer "$id"
        done
        print_final_summary
    else
        # 无参数 -> 运行全部五层（默认过滤 T*/A*）
        if [ -n "${SKIP_PREFIXES:-}" ]; then
            echo -e "${YELLOW}运行全部五层测试（跳过前缀 [${SKIP_PREFIXES}] 的场景）...${NC}"
        else
            echo -e "${YELLOW}运行全部五层测试（包含全部场景）...${NC}"
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

        echo ""
        echo "=========================================="
        echo -e "${BLUE}Layer 5: Performance (port 12345)${NC}"
        echo "=========================================="
        run_layer_with_filter "${SCRIPT_DIR}/layers/performance" || true

        # 汇总
        echo ""
        echo -e "${GREEN}全部层测试执行完毕${NC}"
        print_timing_report
        print_failure_report
        # 将报告落盘到 summary.log
        save_summary_log
        # 打印日志落盘目录
        if [ -n "${E2E_LOG_DIR:-}" ] && [ -d "${E2E_LOG_DIR}" ]; then
            echo -e "${BLUE}日志目录: ${E2E_LOG_DIR}${NC}"
        fi
    fi
}

main "$@"
