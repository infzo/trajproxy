"""
请求上下文传播（ContextVar）

async/await 链自动传播 request_id。
注意：asyncio.to_thread() 不传播，需显式传递。
"""

from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(request_id: str) -> None:
    """设置当前协程上下文的 request_id"""
    _request_id.set(request_id)


def get_request_id(default: str = "") -> str:
    """获取当前协程上下文的 request_id"""
    return _request_id.get(default)