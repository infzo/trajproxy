#!/bin/bash
# E2E 测试顶层编排器
# 支持按层运行或跨层运行测试用例
#
# 用法:
#   ./run_tests.sh                          运行全部四层（自动启用 CacheInferServer）
#   ./run_tests.sh --no-cache               运行全部四层（不使用缓存）
#   ./run_tests.sh --layer nginx            仅运行 Nginx 层 (port 12345)
#   ./run_tests.sh --layer proxy            仅运行 Proxy 层 (port 12300)
#   ./run_tests.sh F100                     按编号搜索两层运行
#   ./run_tests.sh --layer nginx F100 F101  指定层内特定用例
#
# 缓存说明:
#   默认自动启动 CacheInferServer 缓存推理请求，二次运行极快。
#   使用 --no-cache 禁用缓存，直接访问真实推理服务。

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

# ========================================
# CacheInferServer 配置
# ========================================
CACHE_INFER_SCRIPT="${SCRIPT_DIR}/cache_infer_server.py"
CACHE_INFER_PORT="${CACHE_INFER_PORT:-18999}"
CACHE_INFER_URL="http://127.0.0.1:${CACHE_INFER_PORT}"

# 真实推理服务地址（缓存未命中时转发到的目标）
# 默认使用 BACKEND_MODEL_URL 的原始值
REAL_INFER_URL="${REAL_INFER_URL:-${BACKEND_MODEL_URL:-http://host.docker.internal:8000/v1}}"

# 是否启用缓存（可通过 --no-cache 禁用）
USE_CACHE=true
CACHE_PID=""

# ========================================
# CacheInferServer 生命周期
# ========================================

start_cache_server() {
    # 检查缓存脚本是否存在
    if [ ! -f "$CACHE_INFER_SCRIPT" ]; then
        echo -e "${YELLOW}[WARN] 缓存服务器脚本不存在: ${CACHE_INFER_SCRIPT}，跳过缓存${NC}"
        return 1
    fi

    # 清理占用端口的残留进程
    local occupying_pid
    occupying_pid=$(lsof -ti :"${CACHE_INFER_PORT}" 2>/dev/null)
    if [ -n "$occupying_pid" ]; then
        echo -e "${YELLOW}[WARN] 端口 ${CACHE_INFER_PORT} 被残留进程 (PID: $occupying_pid) 占用，正在清理...${NC}"
        kill -9 $occupying_pid 2>/dev/null
        sleep 1
    fi

    echo -e "${BLUE}[CACHE] 启动 CacheInferServer (port ${CACHE_INFER_PORT})${NC}"
    echo -e "${BLUE}[CACHE]   真实后端: ${REAL_INFER_URL}${NC}"

    REAL_INFER_URL="${REAL_INFER_URL}" \
        python3 "${CACHE_INFER_SCRIPT}" "${CACHE_INFER_PORT}" &
    CACHE_PID=$!

    # 等待服务器就绪
    local health_result
    for i in $(seq 1 15); do
        if ! kill -0 "$CACHE_PID" 2>/dev/null; then
            echo -e "${RED}[ERROR] CacheInferServer 进程异常退出${NC}"
            CACHE_PID=""
            return 1
        fi
        health_result=$(curl -s --noproxy '*' --max-time 3 "${CACHE_INFER_URL}/cache/health" 2>&1)
        if echo "$health_result" | grep -q "ok"; then
            echo -e "${GREEN}[CACHE] CacheInferServer 已就绪 (PID: ${CACHE_PID})${NC}"
            return 0
        fi
        sleep 1
    done

    echo -e "${RED}[ERROR] CacheInferServer 启动超时${NC}"
    kill "$CACHE_PID" 2>/dev/null
    wait "$CACHE_PID" 2>/dev/null
    CACHE_PID=""
    return 1
}

stop_cache_server() {
    if [ -n "$CACHE_PID" ]; then
        echo -e "${BLUE}[CACHE] 停止 CacheInferServer (PID: ${CACHE_PID})${NC}"
        kill "$CACHE_PID" 2>/dev/null
        wait "$CACHE_PID" 2>/dev/null
        CACHE_PID=""

        # 打印缓存统计
        local stats
        stats=$(curl -s --noproxy '*' --max-time 3 "${CACHE_INFER_URL}/cache/stats" 2>/dev/null)
        if [ -n "$stats" ]; then
            local hits misses entries
            hits=$(echo "$stats" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('hits', 0))" 2>/dev/null || echo "?")
            misses=$(echo "$stats" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('misses', 0))" 2>/dev/null || echo "?")
            entries=$(echo "$stats" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('entries', 0))" 2>/dev/null || echo "?")
            echo -e "${GREEN}[CACHE] 统计: ${hits} 命中 / ${misses} 未命中 / ${entries} 缓存条目${NC}"
        fi
    fi
}

# 清理缓存（用于 --no-cache 模式）
clear_cache() {
    curl -s --noproxy '*' --max-time 5 -X DELETE "${CACHE_INFER_URL}/cache/clear" > /dev/null 2>&1 || true
}

# ========================================
# 信号处理（确保退出时清理）
# ========================================
cleanup() {
    echo ""
    echo -e "${YELLOW}[CLEANUP] 正在清理...${NC}"
    stop_cache_server
}
trap cleanup EXIT INT TERM

# 打印帮助
print_usage() {
    echo "用法:"
    echo "  $0                          运行全部四层（自动启用 CacheInferServer）"
    echo "  $0 --no-cache               运行全部四层（不使用缓存）"
    echo "  $0 --layer nginx            仅运行 Nginx 层 (port 12345)"
    echo "  $0 --layer proxy            仅运行 Proxy 层 (port 12300)"
    echo "  $0 --layer archive          仅运行 Archive 层"
    echo "  $0 --layer comparison       仅运行 Comparison 层"
    echo "  $0 --layer cache            仅运行 Cache 层（测试缓存功能本身）"
    echo "  $0 <场景编号>                按编号搜索各层运行 (如: $0 F200)"
    echo "  $0 --layer nginx F100 F101  指定层内特定用例"
    echo ""
    echo "缓存选项:"
    echo "  --no-cache                  不使用缓存，直接访问真实推理服务"
    echo "  CACHE_INFER_PORT=19999      自定义缓存服务器端口"
    echo "  REAL_INFER_URL=...          自定义真实推理服务地址"
    echo ""
    echo "可用层:"
    echo "  nginx      - Nginx 入口层测试 (port 12345)"
    echo "  proxy      - 直连 Proxy 层测试 (port 12300)"
    echo "  archive    - 归档调度层测试"
    echo "  comparison - 对比测试层 (vLLM:8000 vs Proxy:12300)"
    echo "  cache      - CacheInferServer 功能验证"
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
            --no-cache)
                USE_CACHE=false
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
    # 缓存服务器：cache 层自身测试不需要启动缓存
    # ========================================
    if [ "$LAYER" = "cache" ]; then
        USE_CACHE=false
        echo -e "${YELLOW}[CACHE] Cache 层测试自身，不启用全局缓存${NC}"
    fi

    # ========================================
    # 启动缓存（默认启用）
    # ========================================
    if $USE_CACHE; then
        if start_cache_server; then
            # 将 BACKEND_MODEL_URL 指向缓存服务器
            export BACKEND_MODEL_URL="${CACHE_INFER_URL}/v1"
            echo -e "${GREEN}[CACHE] BACKEND_MODEL_URL → ${BACKEND_MODEL_URL}${NC}"
        else
            echo -e "${YELLOW}[CACHE] 缓存服务器启动失败，使用原始 BACKEND_MODEL_URL=${BACKEND_MODEL_URL:-未设置}${NC}"
        fi
        echo ""
    else
        echo -e "${YELLOW}[CACHE] 缓存已禁用，使用原始 BACKEND_MODEL_URL=${BACKEND_MODEL_URL:-未设置}${NC}"
        echo ""
    fi

    # ========================================
    # 运行测试
    # ========================================

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
            comparison|4)
                if [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
                    run_layer "${SCRIPT_DIR}/layers/comparison" "${SCENARIO_IDS[@]}"
                else
                    run_layer "${SCRIPT_DIR}/layers/comparison"
                fi
                TOTAL_SCENARIOS=$?
                ;;
            cache)
                if [ ${#SCENARIO_IDS[@]} -gt 0 ]; then
                    run_layer "${SCRIPT_DIR}/layers/cache" "${SCENARIO_IDS[@]}"
                else
                    run_layer "${SCRIPT_DIR}/layers/cache"
                fi
                TOTAL_SCENARIOS=$?
                ;;
            *)
                echo -e "${RED}未知层: ${LAYER}（可用: nginx, proxy, archive, comparison, cache）${NC}"
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
        # 无参数 -> 运行全部四层
        echo -e "${YELLOW}运行全部四层测试...${NC}"

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

        echo ""
        echo -e "${BLUE}Layer 4: Comparison (vLLM:8000 vs Proxy:12300)${NC}"
        echo "=========================================="
        run_layer "${SCRIPT_DIR}/layers/comparison" || true

        # 汇总
        echo ""
        echo -e "${GREEN}全部层测试执行完毕${NC}"
    fi
}

main "$@"
