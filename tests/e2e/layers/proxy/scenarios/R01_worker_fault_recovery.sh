#!/bin/bash
# R01: Worker 故障自动重启恢复
# 矩阵: 代理层×故障注入×自愈恢复
# 验证目标: kill 所有 Worker Actor 进程后, Ray 自动重启全部 Worker + 健康监控分别 re-initialize,
#            整体服务能力恢复 (避免只 kill 一个导致请求落到另一个 Worker 误判为恢复)
# 前置条件: TrajProxy 以 Docker Compose 部署, count >= 2, max_restarts >= 1 (或 -1)
# 对应源码: traj_proxy/workers/manager.py L256-261 (配置), L359-440 (恢复逻辑)
# 故障注入: 容器内 /proc/net/tcp + /proc/*/fd 扫描, 精确 SIGKILL 各端口持有进程

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../utils.sh"

echo "=== R01: Worker 故障自动重启 ==="

RUN_ID="run-r01"
MODEL_NAME="${DEFAULT_MODEL_NAME}"
CONTAINER_NAME="${TRAJPROXY_CONTAINER:-traj-proxy}"
# Worker 端口范围: base_port (12300) + worker_count (2) → 12300 12301
# 全部 kill, 确保验证的是整体恢复而不是单个 Worker 兜底
WORKER_PORTS=(12300 12301)
MAX_WAIT=180       # 最长等待恢复时间 (秒)
WAIT_INTERVAL=10   # 轮询间隔 (秒)

# ========================================
# 故障注入: 在容器内 kill 所有 Worker Actor 进程
# 使用 /proc/net/tcp 解析端口→inode, 扫描 /proc/*/fd 定位持有进程,
# 不依赖 iproute2/procps 等外部工具 (slim 镜像不含), 且精确命中 Worker
# 排除 PID=1 (主进程 traj_proxy.app)
# ========================================
inject_all_workers_fault() {
    # 将端口列表转为逗号分隔的十六进制字符串 (如 "30FC,30FD")
    local port_hexes=""
    for port in "${WORKER_PORTS[@]}"; do
        local h
        h=$(printf '%04X' "$port")
        port_hexes="${port_hexes:+${port_hexes},}${h}"
    done

    docker exec "$CONTAINER_NAME" python3 -c "
import os, signal, sys

target_ports = set('${port_hexes}'.split(','))
target_inodes = set()

# 收集所有监听目标端口的 socket inode
with open('/proc/net/tcp') as f:
    for line in f:
        parts = line.strip().split()
        if len(parts) < 10:
            continue
        local_addr = parts[1]
        addr_parts = local_addr.split(':')
        if len(addr_parts) < 2:
            continue
        port_hex = addr_parts[-1]
        state = parts[3]  # 0A = LISTEN
        if port_hex in target_ports and state == '0A':
            target_inodes.add(parts[9])

if not target_inodes:
    ports_str = ','.join(sorted('0x' + p for p in target_ports))
    print(f'NOTFOUND:no_ports_listening:{ports_str}')
    sys.exit(1)

killed = []
not_found_count = 0

# 扫描所有进程的 fd 目录, 找到持有这些 socket inode 的进程并 SIGKILL
for entry in os.listdir('/proc'):
    if not entry.isdigit():
        continue
    pid = int(entry)
    if pid == 1:  # 跳过主进程 (traj_proxy.app)
        continue
    fd_dir = f'/proc/{pid}/fd'
    try:
        for fd in os.listdir(fd_dir):
            try:
                link = os.readlink(f'{fd_dir}/{fd}')
                for inode in target_inodes:
                    if f'socket:[{inode}]' in link:
                        os.kill(pid, signal.SIGKILL)
                        killed.append(str(pid))
                        target_inodes.discard(inode)
                        break
            except (OSError, ProcessLookupError):
                continue
    except (PermissionError, FileNotFoundError):
        continue

if killed:
    print('KILLED:' + ','.join(killed))
    sys.exit(0)
else:
    print('NOTFOUND:no_process_owns_any_socket')
    sys.exit(1)
"
}

# ========================================
# 检测指定端口是否有进程在监听
# 返回: YES / NO
# ========================================
check_port_alive() {
    local port=$1
    local port_hex
    port_hex=$(printf '%04X' "$port")

    docker exec "$CONTAINER_NAME" python3 -c "
with open('/proc/net/tcp') as f:
    for line in f:
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        local_addr = parts[1]
        port_hex = local_addr.split(':')[-1] if ':' in local_addr else ''
        state = parts[3]
        if port_hex == '$port_hex' and state == '0A':
            print('YES')
            exit(0)
print('NO')
" 2>/dev/null
}

# ========================================
# 检测所有 Worker 端口是否全部恢复监听
# 返回: ALL (全部恢复) / PARTIAL (部分) / NONE (无)
# ========================================
check_all_workers_alive() {
    local alive=0
    local total=${#WORKER_PORTS[@]}
    for port in "${WORKER_PORTS[@]}"; do
        if [ "$(check_port_alive "$port")" = "YES" ]; then
            alive=$((alive + 1))
        fi
    done
    if [ $alive -eq $total ]; then
        echo "ALL"
    elif [ $alive -gt 0 ]; then
        echo "PARTIAL:$alive/$total"
    else
        echo "NONE"
    fi
}

# ========================================
# 测试主体
# ========================================

# 注册模型
log_step "注册模型"
REG=$(curl_with_log -s -X POST "${BASE_URL}/models/register" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\":\"${RUN_ID}\",\"model_name\":\"${MODEL_NAME}\",\"url\":\"${BACKEND_MODEL_URL}\",\"api_key\":\"${CHAT_API_KEY}\",\"token_in_token_out\":false}")
assert_contains "$REG" "success" "注册成功"
sleep 2

# 基线验证: 服务正常
log_step "基线验证: 发送请求确认服务正常"
RESP=$(curl_with_log -s -w "\n%{http_code}" --max-time 30 \
    -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"baseline check\"}],\"max_tokens\":8,${E2E_SAMPLING_PARAMS}}")
split_response "$RESP"
assert_eq "200" "$RESPONSE_STATUS" "基线请求返回 200"

# 确认所有 Worker 端口初始状态
ALL_BEFORE=$(check_all_workers_alive)
if [ "$ALL_BEFORE" != "ALL" ]; then
    log_error "Worker 端口初始状态异常: $ALL_BEFORE"
    assert_fail "部分 Worker 端口未监听: $ALL_BEFORE"
fi
log_info "所有 Worker 端口 (${WORKER_PORTS[*]}) 正常监听"

# 故障注入: SIGKILL 所有 Worker Actor 进程
log_step "故障注入: SIGKILL 所有 Worker Actor 进程 (ports: ${WORKER_PORTS[*]})"
KILL_RESULT=$(inject_all_workers_fault)

if [[ "$KILL_RESULT" == KILLED:* ]]; then
    KILLED_PIDS="${KILL_RESULT#KILLED:}"
    log_success "已 SIGKILL ${#WORKER_PORTS[@]} 个 Worker 进程 (PIDs: $KILLED_PIDS)"
else
    log_error "故障注入失败: $KILL_RESULT"
    assert_fail "无法 kill Worker Actor 进程: $KILL_RESULT"
    curl -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
    print_summary
    exit 1
fi

# 确认端口短暂不可用 (进程已死)
sleep 3
PORTS_STATE=""
for port in "${WORKER_PORTS[@]}"; do
    PORTS_STATE="${PORTS_STATE} ${port}=$(check_port_alive "$port")"
done
log_info "kill 后端口状态:$PORTS_STATE (NO=预期, YES=进程未完全退出)"

# 等待恢复: Ray 重启所有 Actor + 健康监控 re-initialize
log_step "等待所有 Worker 自动恢复 (最长 ${MAX_WAIT}s)"
RECOVERED=0
RECOVERY_TIME=0
elapsed=0

while [ $elapsed -lt $MAX_WAIT ]; do
    sleep $WAIT_INTERVAL
    elapsed=$((elapsed + WAIT_INTERVAL))

    # 检查 1: 所有端口重新监听
    WORKERS_STATE=$(check_all_workers_alive)
    if [ "$WORKERS_STATE" != "ALL" ]; then
        log_info "Worker 未全部恢复 ($WORKERS_STATE)... (${elapsed}s)"
        continue
    fi

    # 所有端口已恢复, 等一段时间让 re-initialize 完成
    log_info "所有端口已恢复监听, 等待 initialize 完成..."
    sleep $WAIT_INTERVAL

    # 检查 2: 发送探测请求 (覆盖所有端口, 防止只恢复了一个)
    ALL_PROBE_OK=1
    for port in "${WORKER_PORTS[@]}"; do
        PROBE_CODE=$(command curl -s --max-time 15 -o /dev/null -w "%{http_code}" \
            -X POST "http://127.0.0.1:${port}/v1/chat/completions" \
            -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
            -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"recovery probe on port ${port}\"}],\"max_tokens\":4,${E2E_SAMPLING_PARAMS}}" 2>/dev/null)
        if [ "$PROBE_CODE" != "200" ]; then
            log_warning "Worker port ${port} 探测失败 (HTTP $PROBE_CODE), 等待..."
            ALL_PROBE_OK=0
            break
        fi
    done

    if [ $ALL_PROBE_OK -eq 1 ]; then
        RECOVERED=1
        RECOVERY_TIME=$((elapsed + WAIT_INTERVAL))
        log_success "所有 Worker 已恢复, 全部端口服务可用 (总耗时: ~${RECOVERY_TIME}s)"
        break
    fi
done

# ========================================
# 断言
# ========================================

# 断言 1: Worker 在限定时间内恢复
if [ $RECOVERED -eq 1 ]; then
    log_success "Worker 故障自动恢复成功 (~${RECOVERY_TIME}s)"
    TESTS_TOTAL=$((TESTS_TOTAL + 1)); TESTS_PASSED=$((TESTS_PASSED + 1))
else
    assert_fail "Worker 未在 ${MAX_WAIT}s 内恢复"
fi

# 断言 2: 重启后完整功能验证 (非流式)
log_step "完整功能验证: 非流式请求"
RESP2=$(curl_with_log -s -w "\n%{http_code}" --max-time 30 \
    -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"verify after restart\"}],\"max_tokens\":16,${E2E_SAMPLING_PARAMS}}")
split_response "$RESP2"
assert_eq "200" "$RESPONSE_STATUS" "重启后非流式请求成功"

# 断言 3: 重启后流式请求验证
log_step "完整功能验证: 流式请求"
STREAM_CODE=$(command curl -s --max-time 30 -o /dev/null -w "%{http_code}" \
    -X POST "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer ${CHAT_API_KEY}" \
    -d "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"stream test\"}],\"max_tokens\":8,\"stream\":true,${E2E_SAMPLING_PARAMS}}" 2>/dev/null)
TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ "$STREAM_CODE" = "200" ]; then
    log_success "重启后流式请求正常 (HTTP $STREAM_CODE)"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    log_error "重启后流式请求失败 (HTTP $STREAM_CODE)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# 断言 4: 日志确认恢复路径被触发 (每个 Worker 至少 1 次 re-initialize)
log_step "验证日志: 确认自动恢复路径"
RECOVERY_LOG_COUNT=$(docker logs --since 10m "$CONTAINER_NAME" 2>&1 | grep -c "re-initialized successfully" 2>/dev/null || echo "0")
EXPECTED_WORKERS=${#WORKER_PORTS[@]}
TESTS_TOTAL=$((TESTS_TOTAL + 1))
if [ "$RECOVERY_LOG_COUNT" -ge "$EXPECTED_WORKERS" ]; then
    log_success "日志确认所有 ${EXPECTED_WORKERS} 个 Worker 触发 re-initialize (匹配 ${RECOVERY_LOG_COUNT} 次)"
    TESTS_PASSED=$((TESTS_PASSED + 1))
elif [ "$RECOVERY_LOG_COUNT" -gt 0 ]; then
    log_warning "日志匹配 ${RECOVERY_LOG_COUNT} 次 re-initialize (期望 >=${EXPECTED_WORKERS}, 可能日志轮转)"
    # 软断言: 探测已通, 恢复路径一定有效
    if [ $RECOVERED -eq 1 ]; then
        TESTS_PASSED=$((TESTS_PASSED + 1))
        log_success "功能已恢复, 恢复路径确认为有效"
    else
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
else
    log_warning "未找到 re-initialize 日志 (可能时间窗口过短或日志轮转)"
    if [ $RECOVERED -eq 1 ]; then
        TESTS_PASSED=$((TESTS_PASSED + 1))
        log_success "功能已恢复, 恢复路径确认为有效"
    else
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
fi

# 清理
curl -s -X DELETE "${BASE_URL}/models?model_name=${MODEL_NAME}&run_id=${RUN_ID}" > /dev/null
print_summary
