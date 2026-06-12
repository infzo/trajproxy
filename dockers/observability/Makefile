# ============================================================
# TrajProxy 可观测性组件 - 快捷操作
# ============================================================
# 配置源：.env（不提交仓库，参考 .env.example）
# 派生：configs/prometheus/targets.json（由脚本自动生成）
# ============================================================

.PHONY: help start up down logs sync targets add reload remove status

# 默认目标：显示可用命令
help:
	@echo "可用命令:"
	@echo "  make start [IP=\"ip1 ip2 ...\"]  一键部署（支持临时节点 IP，不写回 .env）"
	@echo "  make restart                     重启组件"
	@echo "  make stop                        停止组件"
	@echo "  make sync                        仅重新生成 targets.json"
	@echo "  make add    IP=<IP>              持久追加节点到 .env 的 MONITOR_NODES"
	@echo "  make remove IP=<IP>              持久从 .env 移除节点"
	@echo "  make targets                     查看当前监控节点"
	@echo "  make up                          docker compose up（不自动生成 targets.json）"
	@echo "  make down                        docker compose down"
	@echo "  make logs                        查看日志"
	@echo "  make status                      查看服务状态"
	@echo ""
	@echo "示例:"
	@echo "  make start"
	@echo "  make start IP=\"192.168.1.100 10.0.0.5\""
	@echo "  make add IP=192.168.1.100"
	@echo "  make remove IP=192.168.1.100"

# 一键部署（含环境检查、拉镜像、启动、健康检查）
start:
	@bash ../../scripts/start_docker_observability.sh start $(IP)

# 重启组件
restart:
	@bash ../../scripts/start_docker_observability.sh restart $(IP)

# 停止
stop:
	@bash ../../scripts/start_docker_observability.sh stop

# 仅重新生成 targets.json（不启动容器）
sync:
	@bash ../../scripts/start_docker_observability.sh sync

# 持久追加节点到 .env MONITOR_NODES
add:
ifndef IP
	$(error ❌ 请指定节点 IP: make add IP=<节点IP>)
endif
	@bash ../../scripts/start_docker_observability.sh add-node $(IP)

# 持久移除节点
remove:
ifndef IP
	$(error ❌ 请指定节点 IP: make remove IP=<节点IP>)
endif
	@bash ../../scripts/start_docker_observability.sh remove-node $(IP)

# 启动 docker compose（不自动生成 targets.json，需先 make sync）
up:
	@echo "🚀 启动可观测性组件..."
	docker compose up -d

# 停止 docker compose
down:
	@echo "⏹️  停止可观测性组件..."
	docker compose down

# 查看日志
logs:
	docker compose logs -f --tail=100

# 显示当前监控节点
targets:
	@echo "📋 当前 Prometheus 监控节点:"
	@if [ -f configs/prometheus/targets.json ]; then \
		cat configs/prometheus/targets.json | python3 -m json.tool 2>/dev/null || cat configs/prometheus/targets.json; \
	else \
		echo "(targets.json 尚未生成，请先运行 make sync 或 make start)"; \
	fi

# 热重载 Prometheus 配置（无需重启容器）
reload:
	@echo "🔄 重载 Prometheus 配置..."
	@curl -s -X POST http://localhost:$$PROMETHEUS_PORT/-/reload && echo " ✅ 已重载" || echo " ❌ 重载失败"

# 查看服务状态
status:
	@bash ../../scripts/start_docker_observability.sh sync
	@docker compose ps
