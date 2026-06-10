#!/bin/bash
# Archive Layer 测试运行器
# 用法:
#   ./run_layer.sh           # 运行本层所有测试
#   ./run_layer.sh A101      # 运行指定场景
#   ./run_layer.sh A101 A102 # 运行多个指定场景
#
# 环境变量:
#   E2E_JOBS    - Layer 内并发执行数（默认 1，顺序执行）
#   E2E_LOG_DIR - 日志落盘目录（由 run_tests.sh 创建，单独运行时按需自动创建）

# 注意：使用 LAYER_DIR 而非 SCRIPT_DIR，避免被 utils.sh 的同名全局变量覆盖
LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 加载公共工具
source "${LAYER_DIR}/../../utils.sh"
source "${LAYER_DIR}/utils.sh"

LAYER_NAME="archive"

echo "=== Archive Layer ==="

scenario_ids=()
if [ $# -gt 0 ]; then
    scenario_ids=("$@")
fi

run_scenarios_parallel "$LAYER_NAME" "$LAYER_DIR" "${scenario_ids[@]}"
