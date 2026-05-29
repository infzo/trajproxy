#!/bin/bash
# Docker Compose 启动脚本
# 使用 docker-compose 拉起所有服务容器（litellm、postgresdb、traj_proxy、prometheus）
# 支持参数: start, stop, restart

set -e

# 获取操作参数，默认为 start
ACTION="${1:-start}"

# 切换到 docker-compose 目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_DIR="${SCRIPT_DIR}/../dockers/compose"

# 停止超时时间（秒）
STOP_TIMEOUT=30

# 显示帮助信息
show_help() {
    echo "用法: $0 [start|stop|restart]"
    echo ""
    echo "参数说明:"
    echo "  start   - 启动所有服务（默认）"
    echo "  stop    - 停止所有服务"
    echo "  restart - 重启所有服务"
    echo ""
    echo "示例:"
    echo "  $0              # 启动服务（默认）"
    echo "  $0 start        # 启动服务"
    echo "  $0 stop         # 停止服务"
    echo "  $0 restart      # 重启服务"
    echo ""
    echo "说明:"
    echo "  此脚本使用 docker-compose 管理以下服务:"
    echo "    - nginx: 反向代理服务"
    echo "    - litellm: LLM 代理服务"
    echo "    - db: PostgreSQL 数据库"
    echo "    - prometheus: 监控服务"
    echo "    - traj_proxy: 核心代理服务"
}

# 验证参数有效性
validate_action() {
    if [[ ! "$ACTION" =~ ^(start|stop|restart)$ ]]; then
        echo "错误: 无效的操作参数 '$ACTION'"
        echo ""
        show_help
        exit 1
    fi
}

# 显示配置信息
show_config() {
    echo "=== TrajProxy Docker Compose 启动脚本 ==="
    echo "操作类型: ${ACTION}"
    echo "Compose 目录: ${COMPOSE_DIR}"
    echo "停止超时: ${STOP_TIMEOUT} 秒"
    echo ""
}

# 切换到 compose 目录
change_to_compose_dir() {
    if [ ! -d "${COMPOSE_DIR}" ]; then
        echo "错误: docker-compose 目录不存在: ${COMPOSE_DIR}"
        exit 1
    fi

    echo "切换到目录: ${COMPOSE_DIR}"
    cd "${COMPOSE_DIR}" || exit 1
    echo ""
}

# 检查 docker-compose 文件
check_compose_file() {
    if [ ! -f "docker-compose.yml" ]; then
        echo "错误: docker-compose.yml 文件不存在"
        exit 1
    fi
}

# 显示服务状态
show_services_status() {
    echo ""
    echo "=== 服务状态 ==="
    docker-compose ps
    echo ""
}

# 清理不属于当前 compose 项目的残留网络
# 解决警告: "a network exists but was not created for project"
cleanup_stale_network() {
    local network_name="traj-proxy-compose-network"

    # 检查网络是否存在
    if docker network ls --format '{{.Name}}' | grep -q "^${network_name}$"; then
        # 检查是否有容器正在使用该网络
        local connected_containers
        connected_containers=$(docker network inspect "${network_name}" --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null | tr -d ' ')

        if [ -z "${connected_containers}" ]; then
            echo "检测到残留网络 '${network_name}'（无容器使用），正在删除..."
            docker network rm "${network_name}" 2>/dev/null || true
            echo "残留网络已清理。"
        else
            echo "网络 '${network_name}' 存在且有容器使用: ${connected_containers}"
            echo "将尝试 docker-compose down 清理..."
        fi
        echo ""
    fi
}

# 检查并清理已运行的服务容器
# 如果存在运行中的容器，先执行 down 操作确保干净部署
cleanup_running_services() {
    # 先清理残留网络（解决 "not created for project" 警告）
    cleanup_stale_network

    # 获取当前 compose 项目下的容器数量（包括运行和停止的）
    local running_containers
    running_containers=$(docker-compose ps -q 2>/dev/null | wc -l | tr -d ' ')

    if [ "${running_containers}" -gt 0 ]; then
        echo "检测到 ${running_containers} 个已存在的容器，先进行清理..."
        docker-compose down --timeout ${STOP_TIMEOUT}
        echo "已清理完毕，准备重新部署。"
        echo ""
    else
        echo "未检测到已运行的容器，直接部署。"
        echo ""
    fi
}

# 启动所有服务
start_service() {
    echo "=== 启动 TrajProxy 服务 ==="
    echo ""

    # 检查 compose 文件
    check_compose_file

    # 检查并清理已运行的服务，确保干净部署
    cleanup_running_services

    # 启动服务
    echo "启动所有服务..."
    docker-compose up -d

    # 显示服务状态
    show_services_status

    echo "=== TrajProxy 服务已启动 ==="
    echo "查看日志: docker-compose logs -f"
    echo "停止服务: $0 stop"
    echo "重启服务: $0 restart"
    echo "查看状态: docker-compose ps"
}

# 停止所有服务
stop_service() {
    echo "=== 停止 TrajProxy 服务 ==="
    echo ""

    # 检查 compose 文件
    check_compose_file

    # 停止服务
    echo "停止所有服务..."
    docker-compose down --timeout ${STOP_TIMEOUT}

    echo ""
    echo "=== TrajProxy 服务已停止 ==="
    echo "说明: 容器已停止并删除，数据卷已保留"
}

# 重启所有服务
restart_service() {
    echo "=== 重启 TrajProxy 服务 ==="
    echo ""

    # 先停止服务
    stop_service
    echo ""

    # 等待所有容器完全停止
    echo "等待所有容器完全停止..."
    local wait_time=0
    while docker-compose ps -q 2>/dev/null | grep -q .; do
        sleep 1
        wait_time=$((wait_time + 1))
        if [ $wait_time -ge ${STOP_TIMEOUT} ]; then
            echo "警告: 等待超时"
            break
        fi
    done
    echo ""

    # 再启动服务
    start_service
}

# 主流程
main() {
    # 验证参数
    validate_action

    # 显示配置信息
    show_config

    # 切换到 compose 目录
    change_to_compose_dir

    # 根据操作类型执行相应函数
    case "$ACTION" in
        start)
            start_service
            ;;
        stop)
            stop_service
            ;;
        restart)
            restart_service
            ;;
    esac
}

# 执行主流程
main "$@"
