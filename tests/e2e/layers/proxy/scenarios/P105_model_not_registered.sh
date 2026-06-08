#!/bin/bash
# P105: 模型未注册（新增）
# 矩阵: 基础API×模型管理×查询

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== P105: 模型未注册 ==="

log_step "请求未注册模型"
CHAT=$(curl_with_log -s -w "\n%{http_code}" -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"unregistered_model_xyz\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"max_tokens\":10}")
STATUS=$(echo "$CHAT" | sed -n '$p')
if [ "$STATUS" = "404" ] || [ "$STATUS" = "400" ]; then
    log_success "未注册模型返回 $STATUS"
else
    log_error "未注册模型应返回 404/400，实际: $STATUS"
fi

print_summary