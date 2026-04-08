#!/bin/bash
# 本地开发启动脚本
# 用于本地开发环境，连接外部运行的数据库

set -e

# 切换到项目根目录
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# 检查 Python 是否可用
if ! command -v python &> /dev/null; then
    if ! command -v python3 &> /dev/null; then
        echo "错误: Python 未安装，请先安装 Python 3.8+"
        exit 1
    fi
    alias python=python3
fi

# 检查虚拟环境
if [ -d "$PROJECT_ROOT/.venv" ]; then
    echo "发现虚拟环境 .venv，正在激活..."
    source "$PROJECT_ROOT/.venv/bin/activate"
elif [ -z "$VIRTUAL_ENV" ]; then
    echo "警告: 未检测到虚拟环境，建议创建虚拟环境以隔离依赖"
fi

# 设置环境变量
export RAY_WORKING_DIR="."
export RAY_PYTHONPATH="."

# 启动 TrajProxy
echo "启动 TrajProxy..."
python -m traj_proxy.app
