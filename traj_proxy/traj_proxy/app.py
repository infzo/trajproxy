"""
TrajProxy 主入口文件

加载配置并启动WorkerManager
"""

import sys
import time
import signal
import yaml
import asyncio
import ray
from pathlib import Path
from traj_proxy.workers.manager import WorkerManager
from traj_proxy.utils.logger import get_logger

# 添加当前目录到Python路径，确保 Ray Actor 能找到 traj_proxy 模块
traj_proxy_dir = Path(__file__).parent
sys.path.insert(0, str(traj_proxy_dir))

# 设置环境变量，Ray Actor 会继承这些环境变量
sys.path.insert(0, str(traj_proxy_dir))

# 初始化日志系统
logger = get_logger(__name__)


def load_config(config_path: str = "workers.yaml") -> dict:
    """
    加载Worker配置

    参数:
        config_path: 配置文件路径

    返回:
        配置字典
    """
    config_file = Path(__file__).parent / config_path
    if not config_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_file}")

    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        logger.info(f"配置加载成功: {config_file}")
        return config


async def main():
    """
    主函数

    加载配置并启动所有Worker
    """
    logger.info("=" * 50)
    logger.info("TrajProxy Worker Manager 启动中...")
    logger.info("=" * 50)

    # 加载配置
    try:
        config = load_config()
    except FileNotFoundError as e:
        logger.error(f"错误: {e}")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"配置文件解析错误: {e}")
        sys.exit(1)

    # 创建管理器
    manager = WorkerManager(config)

    # 启动所有Worker
    await manager.start_workers()

    logger.info("=" * 50)
    logger.info("TrajProxy Worker Manager 已启动")
    logger.info("=" * 50)
    logger.info(f"ProxyCore Workers: {len(manager.proxy_workers)} 个")
    logger.info(f"TranscriptProvider Workers: {len(manager.transcript_workers)} 个")
    logger.info("=" * 50)

    # 打印状态信息
    status = manager.get_worker_status_sync()
    logger.info("Worker 状态:")
    for worker_type, workers in status.items():
        logger.info(f"{worker_type}:")
        for worker in workers:
            logger.info(f"  - Worker {worker['worker_id']}: {worker['status']} (端口: {worker['port']})")
    logger.info("")

    # 设置信号处理
    def signal_handler(signum, frame):
        logger.warning(f"接收到信号 {signum}，正在关闭...")
        manager.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 保持主进程运行
    try:
        while True:
            time.sleep(10)
            # 可选：定期检查Worker状态
            # status = manager.get_worker_status_sync()
    except KeyboardInterrupt:
        logger.warning("用户中断，正在关闭...")
        manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
