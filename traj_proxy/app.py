"""
TrajProxy 主入口文件

加载配置并启动WorkerManager
"""

import sys
import time
import signal
import asyncio
import traceback
import ray
from pathlib import Path
from traj_proxy.workers.manager import WorkerManager
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import load_config, get_config

# 添加项目根目录到Python路径，确保 Ray Actor 能找到 traj_proxy 模块
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 初始化日志系统
logger = get_logger(__name__)


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
        logger.info(f"配置加载成功")
    except FileNotFoundError as e:
        logger.error(f"错误: {e}\n{traceback.format_exc()}")
        sys.exit(1)

    # 创建管理器
    manager = WorkerManager(config)

    # 启动所有Worker
    await manager.start_workers()

    logger.info("=" * 50)
    logger.info("TrajProxy Worker Manager 已启动")
    logger.info("=" * 50)
    logger.info(f"ProxyWorkers: {len(manager.workers)} 个")
    logger.info("=" * 50)

    # 异步打印状态信息
    status = await manager.get_worker_status()
    logger.info("Worker 状态:")
    for worker in status["workers"]:
        logger.info(f"  - Worker {worker['worker_id']}: {worker['status']} (端口: {worker['port']})")
    logger.info("")

    # 设置信号处理
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.warning(f"接收到信号 {signum}，正在关闭...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 等待关闭信号
    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.warning("用户中断，正在关闭...")

    manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
