"""
TrajProxy 可观测性模块

提供一键启用/禁用全部观测能力：
- Prometheus metrics
- 结构化 JSON 日志
- 请求生命周期追踪
- 分层健康检查

使用方式：
    import traj_proxy.observability
    traj_proxy.observability.setup(app)
    # ...
    traj_proxy.observability.teardown()
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)
_initialized = False


def setup(app: Optional[Any] = None) -> None:
    """初始化可观测性系统（Worker 启动时调用一次）

    Args:
        app: FastAPI app 实例（用于注入健康检查等）
    """
    global _initialized
    if _initialized:
        return

    import os
    if os.getenv("OBSERVABILITY_ENABLED", "true").lower() != "true":
        logger.info("Observability disabled via OBSERVABILITY_ENABLED env")
        return

    from traj_proxy.observability.event_bus import enable
    from traj_proxy.observability import metrics_collector
    from traj_proxy.observability import request_summary
    from traj_proxy.observability import label_guards

    enable()
    _initialized = True  # 先置标志，防止 register_all 异常时重复进入
    try:
        metrics_collector.register_all()
        request_summary.register()
        label_guards.init_known_models()
    except Exception as e:
        _initialized = False
        logger.error(f"Observability initialization failed: {e}")
        raise

    logger.info("Observability system initialized")


def teardown() -> None:
    """关闭可观测性系统"""
    from traj_proxy.observability.event_bus import disable, reset
    disable()
    reset()
    global _initialized
    _initialized = False
    logger.info("Observability system teardown complete")


def disable() -> None:
    """紧急关闭全部观测（业务功能不受影响）"""
    from traj_proxy.observability.event_bus import disable as _disable
    _disable()