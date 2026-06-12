# ============================================================
# TrajProxy 可观测性组件 - 快捷操作
# ============================================================

include .env
export

.PHONY: start up down logs targets add reload remove status

# 一键部署（含环境检查、拉镜像、启动、健康检查）
start:
	@bash ../../scripts/start_docker_observability.sh

# 启动所有服务（不含环境检查）
up:
	@echo "🚀 启动可观测性组件..."
	docker compose up -d
	@echo ""
	@echo "✅ 服务已启动:"
	@echo "   Prometheus:    http://localhost:$${PROMETHEUS_PORT:-19090}"
	@echo "   Grafana:       http://localhost:$${GRAFANA_PORT:-3000} (admin/trajproxy)"
	@echo "   AlertManager:  http://localhost:$${ALERTMANAGER_PORT:-9093}"
	@echo "   Viewer:        http://localhost:$${VIEWER_PORT:-8081} (轨迹回放)"
	@echo ""
	@echo "📌 下一步: make add IP=<节点IP>"

# 停止所有服务
down:
	@echo "⏹️  停止可观测性组件..."
	docker compose down

# 查看日志
logs:
	docker compose logs -f --tail=100

# 显示当前配置的节点目标
targets:
	@echo "📋 当前 TrajProxy 节点目标:"
	@cat configs/prometheus/targets.json | python3 -m json.tool 2>/dev/null || cat configs/prometheus/targets.json

# 添加新节点 (用法: make add IP=192.168.1.100)
add:
ifndef IP
	$(error ❌ 请指定节点 IP: make add IP=<节点IP>)
endif
	@bash ../../scripts/add_node_observability.sh $(IP) $${WORKER_PORT_START:-12300} $${WORKER_PORT_COUNT:-10}

# 移除节点 (用法: make remove IP=192.168.1.100)
remove:
ifndef IP
	$(error ❌ 请指定节点 IP: make remove IP=<节点IP>)
endif
	@bash ../../scripts/remove_node_observability.sh $(IP)

# 热重载 Prometheus 配置（无需重启）
reload:
	@echo "🔄 重载 Prometheus 配置..."
	curl -s -X POST http://localhost:$${PROMETHEUS_PORT:-19090}/-/reload && echo " ✅ 已重载" || echo " ❌ 重载失败"

# 查看服务状态
status:
	docker compose ps
	@echo ""
	@curl -s http://localhost:$${PROMETHEUS_PORT:-19090}/-/healthy > /dev/null 2>&1 && echo "✅ Prometheus: 健康" || echo "❌ Prometheus: 不可用"
	@curl -s http://localhost:$${GRAFANA_PORT:-3000}/api/health > /dev/null 2>&1 && echo "✅ Grafana: 健康" || echo "❌ Grafana: 不可用"
	@curl -s http://localhost:$${ALERTMANAGER_PORT:-9093}/-/healthy > /dev/null 2>&1 && echo "✅ AlertManager: 健康" || echo "❌ AlertManager: 不可用"
	@curl -s http://localhost:$${VIEWER_PORT:-8081}/ > /dev/null 2>&1 && echo "✅ Viewer:       健康" || echo "❌ Viewer:       不可用"
