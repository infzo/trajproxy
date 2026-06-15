"""
TrajProxy 日志配置模块

提供统一的日志配置，支持文件输出和控制台输出
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


class RequestIDFilter(logging.Filter):
    """日志过滤器：注入 request_id 到记录中"""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from traj_proxy.observability.request_context import get_request_id
            record.request_id = get_request_id("")
        except Exception:
            record.request_id = ""
        return True


def get_logger(
    name: str,
    log_level: str = None,
    log_dir: str = None,
    worker_id: str = None
) -> logging.Logger:
    """
    获取配置好的日志记录器

    参数:
        name: 日志记录器名称（通常使用 __name__）
        log_level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        log_dir: 日志文件目录路径
        worker_id: Worker ID，用于日志标识

    返回:
        配置好的 Logger 实例
    """
    # 从环境变量获取日志级别
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    # 设置日志目录
    if log_dir is None:
        log_dir = os.getenv("LOG_DIR", "/app/logs")

    # 创建日志记录器
    logger = logging.getLogger(name)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 设置日志级别
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # 创建格式化器
    # 格式：时间戳 | 日志级别 | 模块名 | WorkerID | 消息
    log_format = os.environ.get("LOG_FORMAT", "text")
    if log_format == "json":
        from traj_proxy.observability.json_formatter import JsonFormatter
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(module)s | %(worker_id)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    # 尝试创建日志目录并添加文件处理器
    log_path = Path(log_dir)
    file_logging_enabled = False
    try:
        log_path.mkdir(parents=True, exist_ok=True)
        file_logging_enabled = True
    except (OSError, PermissionError) as e:
        # 无法创建日志目录，仅使用控制台日志
        import warnings
        warnings.warn(f"Cannot create log directory {log_dir}: {e}. Falling back to console-only logging.")

    if file_logging_enabled:
        # 创建文件处理器 - 使用 RotatingFileHandler 实现日志轮转
        try:
            file_handler = RotatingFileHandler(
                filename=log_path / "traj_proxy.log",
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,  # 保留 5 个备份
                encoding='utf-8'
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(RequestIDFilter())
            logger.addHandler(file_handler)
        except (OSError, PermissionError) as e:
            import warnings
            warnings.warn(f"Cannot create log file: {e}. Falling back to console-only logging.")

    # 创建控制台处理器 - 输出到标准输出（docker logs）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(RequestIDFilter())
    logger.addHandler(console_handler)

    # 添加 Worker ID 上下文过滤器
    worker_filter = WorkerIDFilter(worker_id or "-")
    logger.addFilter(worker_filter)

    return logger


def update_worker_id(logger: logging.Logger, worker_id: str):
    """
    更新日志记录器的 Worker ID

    参数:
        logger: 日志记录器实例
        worker_id: Worker ID
    """
    for filter_obj in logger.filters:
        if isinstance(filter_obj, WorkerIDFilter):
            filter_obj.worker_id = worker_id


class WorkerIDFilter(logging.Filter):
    """Worker ID 过滤器，用于在日志中添加 Worker ID"""

    def __init__(self, worker_id: str = "-"):
        super().__init__()
        self.worker_id = worker_id

    def filter(self, record):
        record.worker_id = self.worker_id
        return True
