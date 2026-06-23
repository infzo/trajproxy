"""
请求上下文传播（ContextVar）

async/await 链自动传播 request_id、run_id、unique_id。
注意：asyncio.to_thread() 不传播，需显式传递。
"""

from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_run_id: ContextVar[str] = ContextVar("run_id", default="")
_unique_id: ContextVar[str] = ContextVar("unique_id", default="")


def set_request_id(request_id: str) -> None:
    """设置当前协程上下文的 request_id"""
    _request_id.set(request_id)


def get_request_id(default: str = "") -> str:
    """获取当前协程上下文的 request_id"""
    return _request_id.get(default)


def set_run_id(run_id: str) -> None:
    """设置当前协程上下文的 run_id"""
    _run_id.set(run_id)


def get_run_id(default: str = "") -> str:
    """获取当前协程上下文的 run_id"""
    return _run_id.get(default)


def set_unique_id(unique_id: str) -> None:
    """设置当前协程上下文的 unique_id"""
    _unique_id.set(unique_id)


def get_unique_id(default: str = "") -> str:
    """获取当前协程上下文的 unique_id"""
    return _unique_id.get(default)


def set_request_context(request_id: str, run_id: str = "", unique_id: str = "") -> None:
    """一次性设置当前协程上下文的所有 ID（便捷方法）"""
    _request_id.set(request_id)
    _run_id.set(run_id)
    _unique_id.set(unique_id)