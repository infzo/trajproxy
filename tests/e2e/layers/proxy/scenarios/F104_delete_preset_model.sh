#!/bin/bash
# F104: 删除预置模型保护（新增）
# 矩阵: 基础API×模型管理×保护
# 验证目标: DELETE 预置模型 → 拒绝/返回 404
# 预置模型定义在 config.yaml 中，不属于 dynamic configs，不应允许 API 删除

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== F104: 删除预置模型 ==="

PRESET_MODEL="${DEFAULT_MODEL_NAME}"

# 步骤 1: 列出模型，确认预置模型存在
log_step "步骤 1: 列出模型，确认预置模型 ${PRESET_MODEL} 存在"
LIST=$(curl -s -w "\n%{http_code}" -X GET "${API_MODELS}")
LIST_BODY=$(echo "$LIST" | sed '$d'); LIST_STATUS=$(echo "$LIST" | sed -n '$p')
assert_http_status "200" "$LIST_STATUS" "列出模型返回 200"
assert_contains "$LIST_BODY" "$PRESET_MODEL" "模型列表应包含预置模型 ${PRESET_MODEL}"

# 步骤 2: 尝试 DELETE 预置模型（不带 run_id） → 应返回 404
log_step "步骤 2: DELETE 预置模型（model_name=${PRESET_MODEL}, run_id=空）"
DEL1=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${PRESET_MODEL}&run_id=")
DEL1_BODY=$(echo "$DEL1" | sed '$d'); DEL1_STATUS=$(echo "$DEL1" | sed -n '$p')
# 预置模型不在 dynamic configs 中，DELETE 返回 404（拒绝删除）
assert_http_status "404" "$DEL1_STATUS" "删除预置模型应返回 404（不允许删除）"

# 步骤 3: 尝试 DELETE 预置模型（带指定 run_id） → 也应返回 404
log_step "步骤 3: DELETE 预置模型（model_name=${PRESET_MODEL}, run_id=app-001）"
DEL2=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${PRESET_MODEL}&run_id=app-001")
DEL2_BODY=$(echo "$DEL2" | sed '$d'); DEL2_STATUS=$(echo "$DEL2" | sed -n '$p')
# 同样不是 dynamic config，返回 404
assert_http_status "404" "$DEL2_STATUS" "删除预置模型(带run_id)也应返回 404"

# 步骤 4: 再次列出模型，确认预置模型仍然存在（未被删除）
log_step "步骤 4: 再次列出模型，确认预置模型仍存在"
LIST2=$(curl -s -w "\n%{http_code}" -X GET "${API_MODELS}")
LIST2_BODY=$(echo "$LIST2" | sed '$d'); LIST2_STATUS=$(echo "$LIST2" | sed -n '$p')
assert_http_status "200" "$LIST2_STATUS" "再次列出模型返回 200"
assert_contains "$LIST2_BODY" "$PRESET_MODEL" "预置模型 ${PRESET_MODEL} 应仍然存在（未被删除）"

print_summary