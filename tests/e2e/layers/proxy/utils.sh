#!/bin/bash
# Layer 2 utils 包装: 加载共享 utils + proxy 层配置

_LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_LAYER_DIR}/../../utils.sh"
source "${_LAYER_DIR}/config.sh"

# Mock推理服务脚本路径
MOCK_INFER_SERVER="${_LAYER_DIR}/mock_infer_server.py"

# TrajProxy可达的mock服务地址（Docker环境用host.docker.internal）
MOCK_INFER_HOST="${MOCK_INFER_HOST:-host.docker.internal}"

# ========================================
# Mock推理服务辅助函数
# ========================================

# 启动Mock推理服务
# 使用前需设置: MOCK_PORT, MOCK_URL
start_mock() {
    log_info "启动Mock推理服务..."

    # 清理占用端口的残留进程
    local occupying_pid
    occupying_pid=$(lsof -ti :"$MOCK_PORT" 2>/dev/null)
    if [ -n "$occupying_pid" ]; then
        log_warning "端口 $MOCK_PORT 被残留进程 (PID: $occupying_pid) 占用，正在清理..."
        kill -9 $occupying_pid 2>/dev/null
        sleep 1
    fi

    python3 "${MOCK_INFER_SERVER}" "$MOCK_PORT" &
    MOCK_PID=$!

    # 等待mock服务启动
    local health_result
    for i in $(seq 1 10); do
        # 检查进程是否已退出（端口冲突等情况）
        if ! kill -0 "$MOCK_PID" 2>/dev/null; then
            log_error "Mock服务进程异常退出（可能端口 $MOCK_PORT 被占用）"
            MOCK_PID=""
            return 1
        fi

        health_result=$(curl -s --noproxy '*' --max-time 3 "${MOCK_URL}/mock/health" 2>&1)
        # 只检查响应内容是否包含 "ok"，不依赖 curl 退出码
        # （某些环境下 curl 可能返回数据但退出码非零）
        if echo "$health_result" | grep -q "ok"; then
            log_success "Mock服务已启动 (PID: ${MOCK_PID}, Port: ${MOCK_PORT})"
            return 0
        fi
        sleep 1
    done

    # 超时时输出诊断信息
    log_error "Mock服务启动超时"
    log_error "进程状态: $(kill -0 $MOCK_PID 2>&1 && echo "运行中" || echo "已退出")"
    log_error "端口状态: $(lsof -i :$MOCK_PORT 2>/dev/null | head -1 || echo "未被监听")"
    log_error "健康检查结果: $health_result"

    # 清理启动失败的进程
    kill "$MOCK_PID" 2>/dev/null
    wait "$MOCK_PID" 2>/dev/null
    MOCK_PID=""
    return 1
}

# 停止Mock推理服务
stop_mock() {
    if [ -n "$MOCK_PID" ]; then
        kill "$MOCK_PID" 2>/dev/null
        wait "$MOCK_PID" 2>/dev/null
        log_info "Mock服务已停止 (PID: ${MOCK_PID})"
        MOCK_PID=""
    fi
}

# 清空Mock服务的请求记录
clear_mock_records() {
    curl -s --noproxy '*' --max-time 5 -X DELETE "${MOCK_URL}/mock/requests" > /dev/null
}

# 从Mock服务获取推理请求记录
# 参数: $1 - 要检查的body参数列表(空格分隔)
#        $2 - 要检查的header列表(空格分隔)
# 输出: BODY:param=value / HEADER:header=value 格式
verify_infer_request() {
    local body_params="${1:-}"
    local header_params="${2:-}"
    local tmpfile
    tmpfile=$(mktemp)
    curl -s --noproxy '*' --max-time 5 "${MOCK_URL}/mock/requests" > "$tmpfile"

    python3 << PYEOF
import json
import sys

with open("$tmpfile", "r") as f:
    data = json.load(f)

# 找到最后一个推理请求
infer_req = None
for req in reversed(data.get("requests", [])):
    if req["path"] in ["/v1/chat/completions", "/v1/completions"]:
        infer_req = req
        break

if infer_req is None:
    print("ERROR:no_infer_request")
    sys.exit(1)

body = infer_req.get("body", {})
headers = {k.lower(): v for k, v in infer_req.get("headers", {}).items()}

# 输出body参数
for param in "$body_params".split():
    if param in body:
        print(f"BODY:{param}={body[param]}")
    else:
        print(f"BODY:{param}=NOT_FOUND")

# 输出header信息
for h in "$header_params".split():
    if h in headers:
        print(f"HEADER:{h}={headers[h]}")
    else:
        print(f"HEADER:{h}=NOT_FOUND")

# 输出完整body供调试
print(f"BODY_JSON:{json.dumps(body, ensure_ascii=False)}")
PYEOF

    rm -f "$tmpfile"
}
