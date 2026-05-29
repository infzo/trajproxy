#!/bin/bash
# TrajProxy 镜像构建脚本
# 构建并导出 amd64 和 arm64 架构的 Docker 镜像
# 支持构建 compose 和 allinone 两种镜像类型

set -e

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMAGES_DIR="${PROJECT_ROOT}/dockers/images"

# 默认构建类型为 compose
BUILD_TYPE="compose"

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)
            BUILD_TYPE="$2"
            shift 2
            ;;
        --type=*)
            BUILD_TYPE="${1#*=}"
            shift
            ;;
        *)
            VERSION="$1"
            shift
            ;;
    esac
done

# 校验构建类型
case "$BUILD_TYPE" in
    compose)
        DOCKERFILE="${PROJECT_ROOT}/dockers/compose/Dockerfile"
        IMAGE_NAME="traj-proxy"
        ;;
    allinone)
        DOCKERFILE="${PROJECT_ROOT}/dockers/allinone/Dockerfile"
        IMAGE_NAME="traj-proxy-allinone"
        ;;
    *)
        echo "错误: 不支持的构建类型 '$BUILD_TYPE'"
        echo "支持的类型: compose, allinone"
        exit 1
        ;;
esac

# 检查版本号参数
if [ -z "$VERSION" ]; then
    echo "错误: 必须指定版本号"
    echo "用法: $0 [--type compose|allinone] <version>"
    echo "示例:"
    echo "  $0 0.0.9                  # 构建 compose 镜像（默认）"
    echo "  $0 --type allinone 0.0.9  # 构建 allinone 镜像"
    exit 1
fi

# 转换版本号为文件名格式 (0.0.9 -> 009)
VERSION_NUM=$(echo "$VERSION" | tr -d '.' | sed 's/^0*//')
VERSION_NUM=$(printf "%03d" "$VERSION_NUM")

echo "=== 构建参数 ==="
echo "构建类型: $BUILD_TYPE"
echo "版本号: $VERSION"
echo "版本标识: $VERSION_NUM"
echo "Dockerfile: $DOCKERFILE"
echo "镜像名称: $IMAGE_NAME"
echo "输出目录: $IMAGES_DIR"
echo ""

# 切换到项目根目录
cd "$PROJECT_ROOT"

# 确保 images 目录存在
mkdir -p "$IMAGES_DIR"

# 构建 amd64 镜像
echo "=== 构建 amd64 镜像 ==="
docker build --platform linux/amd64 -t ${IMAGE_NAME}-amd64:${VERSION} -f "$DOCKERFILE" .
docker save -o "${IMAGES_DIR}/${IMAGE_NAME}.amd64.${VERSION_NUM}.tar" ${IMAGE_NAME}-amd64:${VERSION}
echo "amd64 镜像已导出: ${IMAGES_DIR}/${IMAGE_NAME}.amd64.${VERSION_NUM}.tar"
echo ""

# 构建 arm64 镜像
echo "=== 构建 arm64 镜像 ==="
docker build --platform linux/arm64 -t ${IMAGE_NAME}-arm64:${VERSION} -f "$DOCKERFILE" .
docker save -o "${IMAGES_DIR}/${IMAGE_NAME}.arm64.${VERSION_NUM}.tar" ${IMAGE_NAME}-arm64:${VERSION}
echo "arm64 镜像已导出: ${IMAGES_DIR}/${IMAGE_NAME}.arm64.${VERSION_NUM}.tar"
echo ""

echo "=== 构建完成 ==="
echo "生成的镜像文件:"
ls -lh "${IMAGES_DIR}/${IMAGE_NAME}."*.${VERSION_NUM}.tar 2>/dev/null || echo "无镜像文件"
