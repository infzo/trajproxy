#!/usr/bin/env bash
# ============================================================
# start_docker_observability.sh — 一键部署 TrajProxy 可观测性组件
#
# 用法: ./scripts/start_docker_observability.sh
#
# 前置条件: Docker + Docker Compose V2
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}/../docker/observability"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

cd "$PROJECT_DIR"

# ── 1. 环境检查 ─────────────────────────────────────────────
echo -e "${CYAN}🔍 检查环境...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ 未安装 Docker，请先安装: https://docs.docker.com/get-docker/${NC}"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ 未安装 Docker Compose V2，请升级 Docker: https://docs.docker.com/compose/install/${NC}"
    exit 1
fi

if ! docker info &> /dev/null; then
    echo -e "${RED}❌ Docker 守护进程未运行，请先启动 Docker${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Docker $(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+') + Compose V2${NC}"

# ── 2. 加载 .env ────────────────────────────────────────────
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
    echo -e "${GREEN}✅ 已加载 .env 配置${NC}"
else
    echo -e "${YELLOW}⚠️  未找到 .env，使用默认值${NC}"
fi

PROM_PORT="${PROMETHEUS_PORT:-19090}"
GRAF_PORT="${GRAFANA_PORT:-3000}"
ALERT_PORT="${ALERTMANAGER_PORT:-9093}"

# ── 3. 拉取镜像 ─────────────────────────────────────────────
echo ""
echo -e "${CYAN}📦 拉取镜像...${NC}"
docker compose pull

# ── 4. 启动服务 ─────────────────────────────────────────────
echo ""
echo -e "${CYAN}🚀 启动服务...${NC}"
docker compose up -d

# ── 5. 等待健康检查 ─────────────────────────────────────────
echo ""
echo -e "${CYAN}⏳ 等待服务就绪...${NC}"

wait_for_service() {
    local name="$1"
    local url="$2"
    local max_retries="$3"
    local count=0

    while [ $count -lt "$max_retries" ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            echo -e "  ${GREEN}✅ ${name}${NC}"
            return 0
        fi
        count=$((count + 1))
        sleep 2
    done
    echo -e "  ${YELLOW}⏳ ${name}（仍在启动，请稍后检查）${NC}"
    return 1
}

wait_for_service "Prometheus"   "http://localhost:${PROM_PORT}/-/healthy"  30 &
wait_for_service "AlertManager" "http://localhost:${ALERT_PORT}/-/healthy" 30 &
wait_for_service "Grafana"      "http://localhost:${GRAF_PORT}/api/health" 30 &
wait

# ── 6. 输出结果 ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ 可观测性组件已部署${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Prometheus:${NC}    http://localhost:${PROM_PORT}"
echo -e "  ${CYAN}Grafana:${NC}       http://localhost:${GRAF_PORT}  ${YELLOW}(admin / ${GRAFANA_ADMIN_PASSWORD:-trajproxy})${NC}"
echo -e "  ${CYAN}AlertManager:${NC}  http://localhost:${ALERT_PORT}"
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "  下一步操作:"
echo ""
echo -e "  ${CYAN}添加节点:${NC}  bash scripts/add_node_observability.sh <节点IP>"
echo -e "             # 例如: bash scripts/add_node_observability.sh 192.168.1.100"
echo ""
echo -e "  ${CYAN}停止服务:${NC}  cd docker/observability && docker compose down"
echo -e "  ${CYAN}查看状态:${NC}  cd docker/observability && docker compose ps"
echo -e "  ${CYAN}查看日志:${NC}  cd docker/observability && docker compose logs -f"
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
