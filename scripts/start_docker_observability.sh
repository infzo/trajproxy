#!/usr/bin/env bash
# ============================================================
# start_docker_observability.sh — TrajProxy 可观测性组件管理
#
# 配置单源真相：dockers/observability/.env（不提交）
# 派生文件：configs/prometheus/targets.json（由本脚本自动生成）
#
# 用法:
#   ./scripts/start_docker_observability.sh <ACTION> [OPTIONS]
#
# 子命令:
#   start [IP1 IP2 ...]                     一键部署（可传临时节点 IP，不写回 .env）
#   stop                                    停止组件
#   restart [IP1 IP2 ...]                   重启组件（支持临时节点 IP）
#   add-node <IP> [PORT_START] [PORT_COUNT] 持久追加节点到 .env MONITOR_NODES
#   remove-node <IP>                        持久移除节点
#   sync                                    仅重新生成 targets.json
#
# 示例:
#   ./scripts/start_docker_observability.sh start                        # 用 .env 中的 MONITOR_NODES
#   ./scripts/start_docker_observability.sh start 192.168.1.100 10.0.0.5 # 临时覆盖
#   ./scripts/start_docker_observability.sh add-node 192.168.1.100       # 持久追加
# ============================================================

set -euo pipefail

# ── 常量 ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}/../dockers/observability"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_TEMPLATE="${PROJECT_DIR}/.env.example"
TARGETS_FILE="${PROJECT_DIR}/configs/prometheus/targets.json"
STOP_TIMEOUT=30

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── 帮助信息 ────────────────────────────────────────────────
show_help() {
    echo "用法: $0 <start|stop|restart|add-node|remove-node|sync> [OPTIONS]"
    echo ""
    echo "子命令:"
    echo "  start [IP1 IP2 ...]                     一键部署可观测性组件"
    echo "                                          - 无参数：使用 .env 中的 MONITOR_NODES"
    echo "                                          - 有参数：临时覆盖节点（不写回 .env）"
    echo "  stop                                    停止可观测性组件"
    echo "  restart [IP1 IP2 ...]                   重启可观测性组件（支持临时节点 IP）"
    echo "  add-node <IP> [PORT_START] [PORT_COUNT] 追加节点到 .env 的 MONITOR_NODES"
    echo "  remove-node <IP>                        从 .env 的 MONITOR_NODES 移除节点"
    echo "  sync                                    仅根据 MONITOR_NODES 重新生成 targets.json"
    echo ""
    echo "示例:"
    echo "  $0 start                                 # 使用 .env 配置"
    echo "  $0 start 192.168.1.100 10.0.0.5          # 临时指定两个节点"
    echo "  $0 add-node 192.168.1.100                # 持久追加节点"
    echo "  $0 remove-node 192.168.1.100             # 持久移除节点"
    echo "  $0 stop"
    echo ""
    echo "配置："
    echo "  配置源：${ENV_FILE} （不提交仓库，参考 ${ENV_TEMPLATE}）"
    echo "  派生：  ${TARGETS_FILE} （本脚本自动生成，不提交仓库）"
}

# ── 加载 .env（缺失时用模板兜底）──────────────────────────────
load_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        if [[ -f "$ENV_TEMPLATE" ]]; then
            echo -e "${YELLOW}⚠️  .env 不存在，从 .env.example 创建${NC}"
            cp "$ENV_TEMPLATE" "$ENV_FILE"
        else
            echo -e "${RED}❌ .env 和 .env.example 都不存在: ${PROJECT_DIR}${NC}"
            exit 1
        fi
    fi

    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a

    # 默认值兜底（防止用户注释了某行）
    PROMETHEUS_PORT="${PROMETHEUS_PORT:-19090}"
    GRAFANA_PORT="${GRAFANA_PORT:-3000}"
    ALERTMANAGER_PORT="${ALERTMANAGER_PORT:-9093}"
    VIEWER_PORT="${VIEWER_PORT:-8081}"
    GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
    GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-trajproxy}"
    WORKER_PORT_START="${WORKER_PORT_START:-12300}"
    WORKER_PORT_COUNT="${WORKER_PORT_COUNT:-10}"
    PROMETHEUS_RETENTION="${PROMETHEUS_RETENTION:-30d}"
    PROMETHEUS_RETENTION_SIZE="${PROMETHEUS_RETENTION_SIZE:-50GB}"
    PROMETHEUS_TARGET_HOST="${PROMETHEUS_TARGET_HOST:-host.docker.internal}"
    MONITOR_NODES="${MONITOR_NODES:-}"
}

# ── 从 MONITOR_NODES 生成 targets.json ────────────────────────
# 输入变量：MONITOR_NODES（空格分隔 IP）、WORKER_PORT_START、WORKER_PORT_COUNT、PROMETHEUS_TARGET_HOST
# 把 localhost / 127.0.0.1 替换为 PROMETHEUS_TARGET_HOST（容器内可达宿主标识）
generate_targets_json() {
    # 支持临时覆盖：调用方可通过 TEMP_NODES 数组覆盖
    local nodes="${MONITOR_NODES}"
    if [[ -n "${TEMP_NODES_OVERRIDE:-}" ]]; then
        nodes="$TEMP_NODES_OVERRIDE"
    fi

    local start="${WORKER_PORT_START}"
    local count="${WORKER_PORT_COUNT}"
    local host_alias="${PROMETHEUS_TARGET_HOST}"

    mkdir -p "$(dirname "$TARGETS_FILE")"

    # 空 nodes：写空数组
    if [[ -z "${nodes// /}" ]]; then
        printf '[]\n' > "$TARGETS_FILE"
        echo -e "${YELLOW}⚠️  MONITOR_NODES 为空，已生成空 targets.json${NC}"
        return 0
    fi

    local entries=""
    local node_count=0
    for node in $nodes; do
        # localhost / 127.0.0.1 映射到 docker 可达宿主机别名
        local target_host="$node"
        if [[ "$node" == "localhost" || "$node" == "127.0.0.1" ]]; then
            target_host="$host_alias"
        fi

        # 构建 targets 数组
        local targets=""
        for ((i=0; i<count; i++)); do
            local port=$((start + i))
            if [[ -n "$targets" ]]; then
                targets="${targets}, \"${target_host}:${port}\""
            else
                targets="\"${target_host}:${port}\""
            fi
        done

        local entry
        entry=$(printf '  {\n    "targets": [%s],\n    "labels": {\n      "job": "trajproxy",\n      "node": "%s"\n    }\n  }' "$targets" "$node")

        if [[ -n "$entries" ]]; then
            entries="${entries},
${entry}"
        else
            entries="${entry}"
        fi
        node_count=$((node_count + 1))
    done

    printf '[\n%s\n]\n' "$entries" > "$TARGETS_FILE"
    echo -e "${GREEN}✅ 已生成 targets.json (${node_count} 个节点, 每节点 ${count} 个端口 ${start}-$((start+count-1)))${NC}"
    echo -e "   ${CYAN}节点列表:${NC} $nodes"
}

# ── 持久化节点到 .env 的 MONITOR_NODES ────────────────────────
persist_add_node() {
    local ip="$1"
    load_env

    # 校验 IP 格式（简单宽松校验）
    if ! echo "$ip" | grep -qE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' && [[ "$ip" != "localhost" ]]; then
        echo -e "${RED}❌ 无效的 IP 地址或主机名: ${ip}${NC}"
        exit 1
    fi

    # 检查是否已存在
    for existing in $MONITOR_NODES; do
        if [[ "$existing" == "$ip" ]]; then
            echo -e "${YELLOW}⚠️  节点 $ip 已存在于 MONITOR_NODES，跳过${NC}"
            return 0
        fi
    done

    local new_nodes
    if [[ -z "$MONITOR_NODES" ]]; then
        new_nodes="$ip"
    else
        new_nodes="${MONITOR_NODES} ${ip}"
    fi

    # 写回 .env
    sed -i.bak "s|^MONITOR_NODES=.*|MONITOR_NODES=\"${new_nodes}\"|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
    # 同步内存变量，确保后续 generate_targets_json 看到最新值
    MONITOR_NODES="$new_nodes"
    echo -e "${GREEN}✅ 已持久追加 $ip 到 MONITOR_NODES${NC}"

    # 立即重新生成 targets.json
    generate_targets_json
}

persist_remove_node() {
    local ip="$1"
    load_env

    local new_nodes=""
    local found=0
    for existing in $MONITOR_NODES; do
        if [[ "$existing" == "$ip" ]]; then
            found=1
        else
            new_nodes="${new_nodes}${new_nodes:+ }${existing}"
        fi
    done

    if [[ "$found" -eq 0 ]]; then
        echo -e "${YELLOW}⚠️  未找到节点 $ip${NC}"
        return 0
    fi

    sed -i.bak "s|^MONITOR_NODES=.*|MONITOR_NODES=\"${new_nodes}\"|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
    # 同步内存变量
    MONITOR_NODES="$new_nodes"
    echo -e "${GREEN}✅ 已从 MONITOR_NODES 移除 $ip${NC}"

    generate_targets_json
}

# ── 显示配置信息 ────────────────────────────────────────────
show_config() {
    echo -e "${CYAN}=== TrajProxy 可观测性组件管理 ===${NC}"
    echo -e "操作类型: ${ACTION}"
    echo -e "项目目录: ${PROJECT_DIR}"
    [[ -n "${TEMP_NODES_OVERRIDE:-}" ]] && echo -e "临时节点: ${TEMP_NODES_OVERRIDE}"
    echo ""
}

# ═══════════════════════════════════════════════════════════
#  start — 一键部署可观测性组件
# ═══════════════════════════════════════════════════════════

start_service() {
    load_env

    echo ""
    echo -e "${CYAN}🔍 检查环境...${NC}"

    command -v docker &>/dev/null || {
        echo -e "${RED}❌ 未安装 Docker，请先安装: https://docs.docker.com/get-docker/${NC}"
        exit 1
    }
    docker compose version &>/dev/null || {
        echo -e "${RED}❌ 未安装 Docker Compose V2，请升级 Docker${NC}"
        exit 1
    }
    docker info &>/dev/null || {
        echo -e "${RED}❌ Docker 守护进程未运行${NC}"
        exit 1
    }
    echo -e "${GREEN}✅ Docker $(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+') + Compose V2${NC}"

    # 生成 targets.json（从 MONITOR_NODES / TEMP_NODES）
    echo ""
    echo -e "${CYAN}📝 生成 Prometheus targets.json...${NC}"
    generate_targets_json

    # 预创建持久化数据目录
    echo ""
    echo -e "${CYAN}📂 预创建数据持久化目录...${NC}"
    mkdir -p "${PROJECT_DIR}/data/prometheus" "${PROJECT_DIR}/data/grafana" "${PROJECT_DIR}/data/alertmanager"
    if [[ "$(uname)" != "Darwin" ]]; then
        chown -R 472:472 "${PROJECT_DIR}/data/grafana" 2>/dev/null || true
    fi
    echo -e "${GREEN}✅ 数据目录就绪${NC}"

    # 启动服务（--pull=missing 仅在镜像不存在时拉取，已有则跳过）
    echo ""
    echo -e "${CYAN}🚀 启动服务...${NC}"
    docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/docker-compose.yml" up -d --pull=missing

    # 等待健康检查
    echo ""
    echo -e "${CYAN}⏳ 等待服务就绪...${NC}"
    _wait_for_service "Prometheus"   "http://localhost:${PROMETHEUS_PORT}/-/healthy"  30 &
    _wait_for_service "AlertManager" "http://localhost:${ALERTMANAGER_PORT}/-/healthy" 30 &
    _wait_for_service "Grafana"      "http://localhost:${GRAFANA_PORT}/api/health" 30 &
    wait

    # 尝试热重载 prometheus（容器已启动后）
    sleep 2
    curl -sf -X POST "http://localhost:${PROMETHEUS_PORT}/-/reload" >/dev/null 2>&1 || true

    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✅ 可观测性组件已部署${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${CYAN}Prometheus:${NC}    http://localhost:${PROMETHEUS_PORT}"
    echo -e "  ${CYAN}Grafana:${NC}       http://localhost:${GRAFANA_PORT}  ${YELLOW}(${GRAFANA_ADMIN_USER} / ${GRAFANA_ADMIN_PASSWORD})${NC}"
    echo -e "  ${CYAN}AlertManager:${NC}  http://localhost:${ALERTMANAGER_PORT}"
    echo -e "  ${CYAN}Viewer:${NC}        http://localhost:${VIEWER_PORT}"
    echo ""
    echo -e "  ${CYAN}监控节点:${NC} ${MONITOR_NODES:-<空>}"
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo -e "  下一步操作:"
    echo ""
    echo -e "  ${CYAN}追加节点:${NC}  bash scripts/start_docker_observability.sh add-node <节点IP>"
    echo -e "  ${CYAN}停止服务:${NC}  bash scripts/start_docker_observability.sh stop"
    echo -e "  ${CYAN}查看日志:${NC}  cd dockers/observability && docker compose logs -f"
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
}

_wait_for_service() {
    local name="$1" url="$2" max_retries="$3"
    local count=0
    while [[ $count -lt $max_retries ]]; do
        if curl -sf "$url" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✅ ${name}${NC}"
            return 0
        fi
        count=$((count + 1))
        sleep 2
    done
    echo -e "  ${YELLOW}⏳ ${name}（仍在启动，请稍后检查）${NC}"
    return 1
}

# ═══════════════════════════════════════════════════════════
#  stop — 停止可观测性组件
# ═══════════════════════════════════════════════════════════

stop_service() {
    if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
        echo -e "${RED}❌ docker-compose.yml 不存在: ${PROJECT_DIR}/docker-compose.yml${NC}"
        exit 1
    fi

    echo -e "${CYAN}⏹️  停止可观测性组件...${NC}"
    docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/docker-compose.yml" down --timeout "$STOP_TIMEOUT"

    echo -e "${GREEN}✅ 可观测性组件已停止（数据已保留）${NC}"
}

# ═══════════════════════════════════════════════════════════
#  restart — 重启可观测性组件
# ═══════════════════════════════════════════════════════════

restart_service() {
    echo -e "${CYAN}🔄 重启可观测性组件...${NC}"
    stop_service

    echo -e "${CYAN}⏳ 等待容器完全停止...${NC}"
    local wait_time=0
    while docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/docker-compose.yml" ps -q 2>/dev/null | grep -q .; do
        sleep 1
        wait_time=$((wait_time + 1))
        [[ $wait_time -ge $STOP_TIMEOUT ]] && {
            echo -e "${YELLOW}⚠️  等待超时，强制继续...${NC}"
            break
        }
    done
    start_service
}

# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

main() {
    ACTION="${1:-start}"

    if [[ ! "$ACTION" =~ ^(start|stop|restart|add-node|remove-node|sync|-h|--help|help)$ ]]; then
        echo -e "${RED}❌ 无效命令: '${ACTION}'${NC}"
        echo ""
        show_help
        exit 1
    fi

    shift 2>/dev/null || true

    case "$ACTION" in
        -h|--help|help)
            show_help
            exit 0
            ;;
        sync)
            show_config
            load_env
            generate_targets_json
            ;;
        start)
            # 其余参数作为临时节点覆盖（仅 start_service 内使用）
            if [[ $# -gt 0 ]]; then
                TEMP_NODES_OVERRIDE="$*"
                export TEMP_NODES_OVERRIDE
            fi
            show_config
            start_service
            ;;
        stop)
            show_config
            stop_service
            ;;
        restart)
            if [[ $# -gt 0 ]]; then
                TEMP_NODES_OVERRIDE="$*"
                export TEMP_NODES_OVERRIDE
            fi
            show_config
            restart_service
            ;;
        add-node)
            NODE_IP="${1:-}"
            PORT_START="${2:-12300}"
            PORT_COUNT="${3:-10}"
            WORKER_PORT_START="$PORT_START"
            WORKER_PORT_COUNT="$PORT_COUNT"
            export WORKER_PORT_START WORKER_PORT_COUNT
            if [[ -z "$NODE_IP" ]]; then
                echo -e "${RED}❌ 请提供节点 IP: $0 add-node <IP> [PORT_START] [PORT_COUNT]${NC}"
                show_help
                exit 1
            fi
            show_config
            persist_add_node "$NODE_IP"
            ;;
        remove-node)
            NODE_IP="${1:-}"
            if [[ -z "$NODE_IP" ]]; then
                echo -e "${RED}❌ 请提供节点 IP: $0 remove-node <IP>${NC}"
                show_help
                exit 1
            fi
            show_config
            persist_remove_node "$NODE_IP"
            ;;
    esac
}

main "$@"
