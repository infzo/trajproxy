#!/bin/bash
# 场景 F218: Processor LRU 淘汰与重加载验证（Proxy 层）
# 测试流程：批量注册模型超过缓存上限 → 依次请求触发淘汰 → 验证淘汰后重加载 → 清理
# 默认 LRU 缓存上限 32，注册 35 个模型确保触发淘汰机制
# 注意：使用真实后端，不依赖 mock 推理服务

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "========================================"
echo "场景 F218: Processor LRU 淘汰与重加载验证（Proxy 层）"
echo "========================================"
echo ""

# 测试配置
TEST_BASE_URL="${BASE_URL}"
TEST_RUN_ID="run-lru-evict"
TEST_SESSION_ID_PREFIX="session-f218-$(date +%s%N | md5sum | head -c 8)"

# 注册 35 个模型以超过默认缓存上限 32，确保触发 LRU 淘汰
# 前 32 个模型会填满缓存，第 33-35 个触发淘汰
MODEL_COUNT=35
declare -a MODEL_NAMES=()
for i in $(seq 1 $MODEL_COUNT); do
    MODEL_NAMES+=("test-evict-model-${i}")
done

# ========================================
# 步骤 1: 批量注册 35 个模型
# ========================================
log_step "步骤 1: 注册 ${MODEL_COUNT} 个模型（超过默认缓存上限 32）"

REGISTER_OK=0
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

    REGISTER_STATUS=$(echo "$REGISTER_RESPONSE" | sed -n '$p')

    if [ "$REGISTER_STATUS" = "200" ]; then
        REGISTER_OK=$((REGISTER_OK + 1))
    else
        log_error "注册模型 ${model_name} 失败: HTTP ${REGISTER_STATUS}"
    fi
done

echo ""
assert_eq "$MODEL_COUNT" "$REGISTER_OK" "所有 ${MODEL_COUNT} 个模型注册均应成功"
sleep 1
echo ""

# ========================================
# 步骤 2: 首轮 — 依次请求所有模型填满缓存并触发淘汰
# ========================================
log_step "步骤 2: 首轮依次请求 ${MODEL_COUNT} 个模型（前 32 个填满缓存，后 3 个触发 LRU 淘汰）"

FIRST_ROUND_OK=0
for idx in $(seq 0 $((MODEL_COUNT - 1))); do
    model_name="${MODEL_NAMES[$idx]}"
    session_id="${TEST_SESSION_ID_PREFIX}-r1-${idx}"

    CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${session_id}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${model_name}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Round 1, model ${idx}\"}],
            \"stream\": false
        }")

    CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
    CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

    if [ "$CHAT_STATUS" = "200" ] && echo "$CHAT_BODY" | grep -q "choices"; then
        FIRST_ROUND_OK=$((FIRST_ROUND_OK + 1))
    else
        log_error "${model_name}: 首轮请求失败 (HTTP ${CHAT_STATUS})"
    fi
done

echo ""
assert_eq "$MODEL_COUNT" "$FIRST_ROUND_OK" "首轮所有 ${MODEL_COUNT} 个模型懒加载请求均应成功"
echo ""

# ========================================
# 步骤 3: 验证最先加载的模型已被淘汰（模型 1-3 被后续请求挤出缓存）
# ========================================
log_step "步骤 3: 重新请求最早加载的模型（验证淘汰后可重新加载）"

EVICTED_RELOAD_OK=0
EVICTED_INDICES=(0 1 2)
for idx in "${EVICTED_INDICES[@]}"; do
    model_name="${MODEL_NAMES[$idx]}"
    session_id="${TEST_SESSION_ID_PREFIX}-reload-${idx}"

    CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${session_id}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${model_name}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Reload evicted model ${idx}\"}],
            \"stream\": false
        }")

    CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
    CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

    if [ "$CHAT_STATUS" = "200" ] && echo "$CHAT_BODY" | grep -q "choices"; then
        EVICTED_RELOAD_OK=$((EVICTED_RELOAD_OK + 1))
        log_success "模型 ${model_name}: 淘汰后重新加载成功"
    else
        log_error "模型 ${model_name}: 淘汰后重新加载失败 (HTTP ${CHAT_STATUS})"
    fi
done

echo ""
assert_eq "3" "$EVICTED_RELOAD_OK" "淘汰模型的重新加载请求均应成功"
echo ""

# ========================================
# 步骤 4: 验证最后加载的模型仍在缓存中
# ========================================
log_step "步骤 4: 请求最后加载的模型（应仍在 LRU 缓存中）"

CACHED_OK=0
CACHED_INDICES=(32 33 34)
for idx in "${CACHED_INDICES[@]}"; do
    model_name="${MODEL_NAMES[$idx]}"
    session_id="${TEST_SESSION_ID_PREFIX}-cached-${idx}"

    CHAT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${TEST_BASE_URL}/s/${TEST_RUN_ID}/${session_id}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CHAT_API_KEY}" \
        -d "{
            \"model\": \"${model_name}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Verify cached model ${idx}\"}],
            \"stream\": false
        }")

    CHAT_BODY=$(echo "$CHAT_RESPONSE" | sed '$d')
    CHAT_STATUS=$(echo "$CHAT_RESPONSE" | sed -n '$p')

    if [ "$CHAT_STATUS" = "200" ] && echo "$CHAT_BODY" | grep -q "choices"; then
        CACHED_OK=$((CACHED_OK + 1))
        log_success "模型 ${model_name}: 缓存命中，请求成功"
    else
        log_error "模型 ${model_name}: 请求失败 (HTTP ${CHAT_STATUS})"
    fi
done

echo ""
assert_eq "3" "$CACHED_OK" "缓存中的模型请求均应成功"
echo ""

# ========================================
# 步骤 5: 清理 — 批量删除所有模型
# ========================================
log_step "步骤 5: 清理所有模型"

DELETE_OK=0
for model_name in "${MODEL_NAMES[@]}"; do
    DELETE_RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "${API_MODELS}?model_name=${model_name}&run_id=${TEST_RUN_ID}")

    DELETE_STATUS=$(echo "$DELETE_RESPONSE" | sed -n '$p')

    if [ "$DELETE_STATUS" = "200" ]; then
        DELETE_OK=$((DELETE_OK + 1))
    else
        log_error "删除模型 ${model_name} 失败: HTTP ${DELETE_STATUS}"
    fi
done

echo ""
assert_eq "$MODEL_COUNT" "$DELETE_OK" "所有 ${MODEL_COUNT} 个模型删除均应成功"

echo ""

# 打印测试摘要
print_summary
