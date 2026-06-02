#!/bin/bash
# Cache 层工具: 加载共享 utils + cache 层配置

_LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_LAYER_DIR}/../../utils.sh"
source "${_LAYER_DIR}/config.sh"

# ========================================
# CacheInferServer 生命周期管理
# ========================================

# 启动 CacheInferServer
# 环境变量依赖:
#   REAL_INFER_URL       - 真实推理服务地址（必需）
#   REAL_INFER_API_KEY   - 真实推理服务 API Key（可选）
#   CACHE_INFER_PORT     - 监听端口（默认 18999）
#   CACHE_STORE_DIR      - 缓存存储目录（可选）
start_cache_server() {
    log_info "启动 CacheInferServer..."

    # 验证必需环境变量
    if [ -z "${REAL_INFER_URL}" ]; then
        log_error "REAL_INFER_URL 未设置，无法启动 CacheInferServer"
        return 1
    fi

    # 清理占用端口的残留进程
    local occupying_pid
    occupying_pid=$(lsof -ti :"${CACHE_INFER_PORT}" 2>/dev/null)
    if [ -n "$occupying_pid" ]; then
        log_warning "端口 ${CACHE_INFER_PORT} 被残留进程 (PID: $occupying_pid) 占用，正在清理..."
        kill -9 $occupying_pid 2>/dev/null
        sleep 1
    fi

    # 启动缓存服务器
    REAL_INFER_URL="${REAL_INFER_URL}" \
    REAL_INFER_API_KEY="${REAL_INFER_API_KEY}" \
    CACHE_STORE_DIR="${CACHE_STORE_DIR:-}" \
        python3 "${CACHE_INFER_SERVER}" "${CACHE_INFER_PORT}" &
    CACHE_PID=$!

    # 等待服务器启动（健康检查）
    local health_result
    for i in $(seq 1 15); do
        # 检查进程是否已退出
        if ! kill -0 "$CACHE_PID" 2>/dev/null; then
            log_error "CacheInferServer 进程异常退出（可能端口 ${CACHE_INFER_PORT} 被占用或配置错误）"
            CACHE_PID=""
            return 1
        fi

        health_result=$(curl -s --noproxy '*' --max-time 3 "${CACHE_INFER_URL}/cache/health" 2>&1)
        if echo "$health_result" | grep -q "ok"; then
            log_success "CacheInferServer 已启动 (PID: ${CACHE_PID}, Port: ${CACHE_INFER_PORT})"
            log_info "  真实后端: ${REAL_INFER_URL}"
            return 0
        fi
        sleep 1
    done

    # 超时诊断
    log_error "CacheInferServer 启动超时"
    log_error "  进程状态: $(kill -0 $CACHE_PID 2>&1 && echo "运行中" || echo "已退出")"
    log_error "  端口状态: $(lsof -i :${CACHE_INFER_PORT} 2>/dev/null | head -1 || echo "未被监听")"
    log_error "  健康检查: $health_result"

    kill "$CACHE_PID" 2>/dev/null
    wait "$CACHE_PID" 2>/dev/null
    CACHE_PID=""
    return 1
}

# 停止 CacheInferServer
stop_cache_server() {
    if [ -n "$CACHE_PID" ]; then
        kill "$CACHE_PID" 2>/dev/null
        wait "$CACHE_PID" 2>/dev/null
        log_info "CacheInferServer 已停止 (PID: ${CACHE_PID})"
        CACHE_PID=""
    fi
}

# 清空 CacheInferServer 的所有缓存
clear_cache() {
    log_info "清空 CacheInferServer 缓存..."
    local result
    result=$(curl -s --noproxy '*' --max-time 5 -X DELETE "${CACHE_INFER_URL}/cache/clear" 2>&1)
    if echo "$result" | grep -q "cleared"; then
        local deleted
        deleted=$(echo "$result" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('deleted', 0))" 2>/dev/null)
        log_success "缓存已清空（删除了 ${deleted:-?} 个条目）"
    else
        log_warning "清空缓存失败: $result"
    fi
}

# 获取缓存统计信息
# 输出格式: hits=N misses=N entries=N
get_cache_stats() {
    curl -s --noproxy '*' --max-time 5 "${CACHE_INFER_URL}/cache/stats" 2>/dev/null
}

# 删除指定缓存条目
# 参数: $1 - 请求哈希值
delete_cache_entry() {
    local hash="$1"
    curl -s --noproxy '*' --max-time 5 -X DELETE "${CACHE_INFER_URL}/cache/entry/${hash}" 2>/dev/null
}
