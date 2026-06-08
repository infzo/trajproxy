#!/bin/bash
# Performance Layer 测试运行器
# 用法:
#   ./run_layer.sh           # 运行本层所有测试
#   ./run_layer.sh T101      # 运行指定场景
#   ./run_layer.sh T101 T102 # 运行多个指定场景
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS_DIR="${SCRIPT_DIR}/scenarios"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=== Performance Layer ==="

run_scenario() {
    local scenario_file="$1"
    local scenario_name=$(basename "$scenario_file" .sh)

    echo ""
    echo "========================================"
    echo -e "${BLUE}[Performance Layer] 运行场景: ${scenario_name}${NC}"
    echo "========================================"

    export FAILURE_CONTEXT="${scenario_name}"
    local start_ts=$(date +%s)
    if bash "$scenario_file"; then
        local end_ts=$(date +%s)
        local duration=$((end_ts - start_ts))
        echo -e "${GREEN}场景 ${scenario_name} 耗时: ${duration}s${NC}"
        if [ -n "${TIMING_LOG:-}" ] && [ -w "${TIMING_LOG:-}" ]; then
            echo "${scenario_name}|${duration}" >> "$TIMING_LOG"
        fi
        return 0
    else
        local end_ts=$(date +%s)
        local duration=$((end_ts - start_ts))
        echo -e "${RED}场景 ${scenario_name} 耗时: ${duration}s (失败)${NC}"
        if [ -n "${TIMING_LOG:-}" ] && [ -w "${TIMING_LOG:-}" ]; then
            echo "${scenario_name}|${duration}" >> "$TIMING_LOG"
        fi
        return 1
    fi
}

if [ $# -gt 0 ]; then
    # 只运行指定的场景
    for scenario_id in "$@"; do
        matched_files=("${SCENARIOS_DIR}/${scenario_id}_"*".sh")
        if [ -f "${matched_files[0]}" ]; then
            run_scenario "${matched_files[0]}" || true
        else
            echo -e "${RED}错误: 找不到场景 ${scenario_id}${NC}"
        fi
    done
else
    # 无参数，运行所有场景
    for f in "${SCENARIOS_DIR}"/*.sh; do
        if [ -f "$f" ]; then
            run_scenario "$f" || true
        fi
    done
fi