#!/bin/bash
# Docker All-in-One 启动脚本
# 单容器部署，包含 nginx + litellm + postgres + traj_proxy
# 支持参数: start, stop, restart

set -e

# 脚本目录和项目目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 容器和镜像配置（向后兼容，支持原有的位置参数）
# 如果第一个参数是 start/stop/restart，则镜像参数从 $2, $3 获取
if [[ "$1" =~ ^(start|stop|restart)$ ]]; then
    # 新模式：sh start_docker_allinone.sh [start|stop|restart] [镜像名] [标签]
    IMAGE_NAME="${2:-traj_proxy_allinone}"
    IMAGE_TAG="${3:-latest}"
    ACTION="$1"
else
    # 旧模式：sh start_docker_allinone.sh [镜像名] [标签] （默认执行 start）
    # 向后兼容：用户可能直接指定镜像名称
    IMAGE_NAME="${1:-traj_proxy_allinone}"
    IMAGE_TAG="${2:-latest}"
    ACTION="start"
fi

CONTAINER_NAME="traj-proxy"
NETWORK_NAME="traj-proxy-allinone-network"
STOP_TIMEOUT=30  # 停止容器的超时时间（秒）

# 显示帮助信息
show_help() {
    echo "用法: $0 [start|stop|restart] [镜像名称] [镜像标签]"
    echo ""
    echo "参数说明:"
    echo "  start        - 启动服务（默认）"
    echo "  stop         - 停止服务"
    echo "  restart      - 重启服务"
    echo "  镜像名称     - Docker 镜像名称（默认: traj_proxy_allinone）"
    echo "  镜像标签     - Docker 镜像标签（默认: latest）"
    echo ""
    echo "参数解析规则:"
    echo "  1. 如果第一个参数是 start/stop/restart，则按新模式解析"
    echo "     例如: $0 start my_image v1.0"
    echo "  2. 如果第一个参数不是 start/stop/restart，则按旧模式解析（向后兼容）"
    echo "     例如: $0 my_image v1.0  （默认执行 start）"
    echo "     例如: $0                （使用默认镜像执行 start）"
    echo ""
    echo "示例:"
    echo "  $0                           # 使用默认配置启动服务"
    echo "  $0 start                     # 启动服务"
    echo "  $0 stop                      # 停止服务"
    echo "  $0 restart                   # 重启服务"
    echo "  $0 start my_image v1.0       # 使用自定义镜像启动"
    echo ""
    echo "说明:"
    echo "  此脚本使用 Docker 部署 All-in-One 容器，包含以下服务:"
    echo "    - nginx: 反向代理服务"
    echo "    - litellm: LLM 代理服务"
    echo "    - postgres: PostgreSQL 数据库"
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
    echo "=== TrajProxy All-in-One 启动脚本 ==="
    echo "操作类型: ${ACTION}"
    echo "项目目录: ${PROJECT_DIR}"
    echo "镜像名称: ${IMAGE_NAME}:${IMAGE_TAG}"
    echo "容器名称: ${CONTAINER_NAME}"
    echo "停止超时: ${STOP_TIMEOUT} 秒"
    echo ""
}

# 检查并构建镜像
ensure_image_exists() {
    if ! docker image inspect "${IMAGE_NAME}:${IMAGE_TAG}" &>/dev/null; then
        echo "镜像不存在，正在构建..."
        "${PROJECT_DIR}/dockers/allinone/scripts/build_image.sh" "${IMAGE_NAME}" "${IMAGE_TAG}"
        echo ""
    fi
}

# 创建必要的目录
create_directories() {
    echo "创建数据目录..."
    mkdir -p "${PROJECT_DIR}/models"
    mkdir -p "${PROJECT_DIR}/logs"
    echo "数据目录创建完成"
    echo ""
}

# 停止并删除旧容器
stop_existing_container() {
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "发现已存在的容器，正在停止..."

        # 尝试优雅停止
        if docker stop "${CONTAINER_NAME}" --timeout ${STOP_TIMEOUT} &>/dev/null; then
            echo "容器已优雅停止"
        else
            echo "警告: 容器停止超时，强制停止..."
            docker kill "${CONTAINER_NAME}" 2>/dev/null || true
        fi

        # 删除容器
        if docker rm "${CONTAINER_NAME}" 2>/dev/null; then
            echo "旧容器已删除"
        fi
        echo ""
    fi
}

# 启动容器
start_container() {
    echo "启动容器..."
    docker run -d --name "${CONTAINER_NAME}" \
        --network "${NETWORK_NAME}" \
        --shm-size=1g \
        --add-host=host.docker.internal:host-gateway \
        -p 12345:12345 \
        -p 12300:12300 \
        -v trajproxy_pgdata:/data/postgres \
        -v trajproxy_logs:/app/logs \
        -v "${PROJECT_DIR}/models:/app/models" \
        "${IMAGE_NAME}:${IMAGE_TAG}"

    echo ""
    echo "=== TrajProxy 服务已启动 ==="
    echo "容器状态: $(docker ps --filter "name=${CONTAINER_NAME}" --format "{{.Status}}")"
    echo "查看日志: docker logs -f ${CONTAINER_NAME}"
    echo "停止服务: $0 stop"
    echo "重启服务: $0 restart"
}

# 停止服务
stop_service() {
    echo "=== 停止 TrajProxy 服务 ==="

    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "正在停止容器 ${CONTAINER_NAME}..."

        # 尝试优雅停止
        if docker stop "${CONTAINER_NAME}" --timeout ${STOP_TIMEOUT} &>/dev/null; then
            echo "容器已优雅停止"
        else
            echo "警告: 容器停止超时，强制停止..."
            docker kill "${CONTAINER_NAME}" 2>/dev/null || true
        fi

        echo ""
        echo "=== TrajProxy 服务已停止 ==="
        echo "说明: 容器已停止并删除，数据卷已保留"

        # 清理专属网络
        cleanup_network
    else
        echo "容器 ${CONTAINER_NAME} 未在运行"
        echo ""
        echo "=== 服务已停止 ==="
    fi
}

# 重启服务
restart_service() {
    echo "=== 重启 TrajProxy 服务 ==="
    echo ""

    # 先停止服务
    stop_service
    echo ""

    # 等待容器完全停止
    echo "等待容器完全停止..."
    local wait_time=0
    while docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; do
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

# 启动服务
start_service() {
    echo "=== 启动 TrajProxy 服务 ==="

    # 检查并构建镜像
    ensure_image_exists

    # 创建必要的目录
    create_directories

    # 确保专属网络存在
    ensure_network_exists

    # 停止并删除旧容器
    stop_existing_container

    # 启动新容器
    start_container
}

# 确保专属网络存在（启动时调用）
ensure_network_exists() {
    if ! docker network ls --format '{{.Name}}' | grep -q "^${NETWORK_NAME}$"; then
        echo "创建专属网络 '${NETWORK_NAME}'..."
        docker network create "${NETWORK_NAME}" 2>/dev/null
        echo "网络已创建"
        echo ""
    fi
}

# 清理专属网络（仅在停止时调用）
cleanup_network() {
    if docker network ls --format '{{.Name}}' | grep -q "^${NETWORK_NAME}$"; then
        echo "清理专属网络 '${NETWORK_NAME}'..."
        docker network rm "${NETWORK_NAME}" 2>/dev/null || true
        echo "网络已清理"
        echo ""
    fi
}

# 主流程
main() {
    # 验证参数
    validate_action

    # 显示配置信息
    show_config

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
