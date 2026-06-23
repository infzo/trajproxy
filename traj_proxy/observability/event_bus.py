"""
进程内同步事件总线

设计约束：
- 同步执行（无 async overhead），subscriber 必须轻量（仅打点/写日志）
- 失败隔离：单个 subscriber 异常不影响其他 subscriber 和业务代码
- 可全局禁用：observability.disable() 一行关闭全部观测
- 无外部依赖，纯 Python 实现
"""

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_subscribers: Dict[str, List[Callable[..., None]]] = defaultdict(list)
_enabled: bool = True


def subscribe(event_name: str, handler: Callable[..., None]) -> None:
    """注册事件订阅者"""
    _subscribers[event_name].append(handler)


def emit(event_name: str, **kwargs: Any) -> None:
    """触发事件，依次调用所有订阅者（失败隔离）"""
    if not _enabled:
        return
    for handler in _subscribers.get(event_name, []):
        try:
            handler(**kwargs)
        except Exception as exc:
            logger.error(f"EventBus 订阅者执行异常 [{event_name}]: {exc}")


def disable() -> None:
    """全局禁用全部观测（紧急关停 / 测试隔离用）"""
    global _enabled
    _enabled = False


def enable() -> None:
    """启用全部观测"""
    global _enabled
    _enabled = True


def reset() -> None:
    """清空所有订阅（测试用）"""
    _subscribers.clear()


def is_enabled() -> bool:
    """返回当前启用状态"""
    return _enabled