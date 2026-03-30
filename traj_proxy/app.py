"""
TrajProxy 主入口文件

加载配置并启动WorkerManager
"""

import sys
import time
import signal
import asyncio
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
        import traceback
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

    # 打印状态信息
    status = manager.get_worker_status_sync()
    logger.info("Worker 状态:")
    for worker in status["workers"]:
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
    except KeyboardInterrupt:
        logger.warning("用户中断，正在关闭...")
        manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
