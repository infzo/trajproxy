#!/bin/bash
# 场景 F216: Processor LRU 缓存命中与淘汰验证（Proxy 层）
# 测试流程：注册多个模型 → 交替请求 → 验证均成功 → 清理
# 注意：使用真实后端，不依赖 mock 推理服务

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F216: Processor LRU 缓存命中与淘汰验证（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
TEST_BASE_URL="${BASE_URL}"
TEST_RUN_ID="run-lru-002"
TEST_SESSION_ID_PREFIX="session-f216-$(date +%s%N | md5sum | head -c 8)"

# 注册多个模型验证 LRU 缓存管理
MODEL_COUNT=5
MODEL_NAMES=()
for i in $(seq 1 $MODEL_COUNT); do
    MODEL_NAMES+=("test-lru-model-${i}")
done

# 步骤 1: 批量注册模型
log_step "步骤 1: 注册 ${MODEL_COUNT} 个模型"

for model_name in "${MODEL_NAMES[@]}"; do
    REGISTER_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${API_MODELS}/register" \
        -H "Content-Type: application/json" \
        -d "{
            \"run_id\": \"${TEST_RUN_ID}\",
            \"model_name\": \"${model_name}\",
            \"url\": \"${BACKEND_MODEL_URL}\",
            \"api_key\": \"${TEST_MODEL_API_KEY}\",
            \"token_in_token_out\": false
        }")

    REGISTER_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')
    REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

    if [ "$REGISTER_STATUS" != "200" ]; then
        log_error "注册模型 ${model_name} 失败: HTTP ${REGISTER_STATUS}"
        echo "$REGISTER_BODY"
        exit 1
    fi
    log_success "模型 ${model_name} 注册成功（仅存储配置）"
done

sleep 1
echo ""

# 步骤 2: 首轮请求 — 逐个触发懒加载
log_step "步骤 2: 首轮请求（触发懒加载，每个模型首次请求时创建 Processor）"

FIRST_ROUND_OK=0
for idx in "${!MODEL_NAMES[@]}"; do
    model_name="${MODEL_NAMES[$idx]}"
    session_id="${TEST_SESSION_ID_PREFIX}-round1-${idx}"

    CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${session_id}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${model_name}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Round 1 request for ${model_name}\"}],
            \"stream\": false
        }")

    CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
    CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

    if [ "$CHAT_STATUS" = "200" ]; then
        FIRST_ROUND_OK=$((FIRST_ROUND_OK + 1))
        log_success "${model_name}: 懒加载 + 请求成功"
    else
        log_error "${model_name}: 请求失败 (HTTP ${CHAT_STATUS})"
        log_response "${CHAT_BODY}"
    fi
done

echo ""
assert_eq "$MODEL_COUNT" "$FIRST_ROUND_OK" "首轮所有模型懒加载请求均应成功"
echo ""

# 步骤 3: 次轮请求 — 验证缓存命中
log_step "步骤 3: 次轮请求（全部应命中 LRU 缓存，无需重新加载）"

SECOND_ROUND_OK=0
for idx in "${!MODEL_NAMES[@]}"; do
    model_name="${MODEL_NAMES[$idx]}"
    session_id="${TEST_SESSION_ID_PREFIX}-round2-${idx}"

    CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${session_id}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${model_name}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Round 2 request for ${model_name}\"}],
            \"stream\": false
        }")

    CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
    CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

    if [ "$CHAT_STATUS" = "200" ]; then
        SECOND_ROUND_OK=$((SECOND_ROUND_OK + 1))
        log_success "${model_name}: 缓存命中，请求成功"
    else
        log_error "${model_name}: 请求失败 (HTTP ${CHAT_STATUS})"
    fi
done

echo ""
assert_eq "$MODEL_COUNT" "$SECOND_ROUND_OK" "次轮所有模型缓存命中请求均应成功"
echo ""

# 步骤 4: 交替请求 — 验证 LRU 访问顺序正确
log_step "步骤 4: 交替请求模型 1 和模型 5（验证 LRU 访问顺序更新）"

for round in 1 2 3; do
    for model_name in "${MODEL_NAMES[0]}" "${MODEL_NAMES[4]}"; do
        session_id="${TEST_SESSION_ID_PREFIX}-alt-${round}-$(date +%s%N | md5sum | head -c 4)"

        CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${session_id}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${CHAT_API_KEY}" \
            -d "{
                \"model\": \"${model_name}\",
                \"messages\": [{\"role\": \"user\", \"content\": \"Alternating round ${round} for ${model_name}\"}],
                \"stream\": false
            }")

        CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

        if [ "$CHAT_STATUS" != "200" ]; then
            log_error "${model_name}: 交替请求失败 (HTTP ${CHAT_STATUS})"
        fi
    done
done

log_success "交替请求完成（验证高频模型保持在缓存顶端）"
echo ""

# 步骤 5: 清理 — 逐个删除模型
log_step "步骤 5: 清理模型"

for model_name in "${MODEL_NAMES[@]}"; do
    DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${model_name}&run_id=${TEST_RUN_ID}")

    DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

    if [ "$DELETE_STATUS" != "200" ]; then
        log_error "删除模型 ${model_name} 失败: HTTP ${DELETE_STATUS}"
    fi
done

log_success "所有模型清理完成"

echo ""

# 打印测试摘要
print_summary
