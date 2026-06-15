"""
观测装饰器 — 透明包裹 InferClient / ProcessorManager 方法
"""

import time
import functools
from typing import Any, Callable

from traj_proxy.observability.event_bus import emit
from traj_proxy.observability.events import (
    EVENT_INFERENCE_COMPLETED,
    EVENT_MODEL_LIFECYCLE,
)
import httpx

from traj_proxy.exceptions import InferTimeoutError, InferServiceError


def classify_infer_error(exc: Exception) -> str:
    """将推理异常分类为 error_type 枚举

    通过回溯 exc.__cause__（原始 httpx 异常）精确分类，
    不依赖错误消息字符串匹配。

    error_type 枚举（6 种）：
    - connect_timeout: 连接超时（默认 60s）
    - read_timeout: 读取超时（默认 600s，模型处理太慢）
    - connection: 连接失败（DNS 解析 / 拒绝连接 / 网络中断）
    - rate_limited: 推理服务返回 429
    - http_error: 推理服务返回其他 HTTP 错误
    - unknown: 兜底
    """
    cause = exc.__cause__

    if isinstance(exc, InferTimeoutError):
        if isinstance(cause, httpx.ConnectTimeout):
            return "connect_timeout"
        return "read_timeout"

    if isinstance(exc, InferServiceError):
        if isinstance(cause, httpx.HTTPStatusError):
            if cause.response.status_code == 429:
                return "rate_limited"
            return "http_error"
        # RequestError / ConnectError 等网络层错误
        return "connection"

    return "unknown"


def observe_inference(func: Callable) -> Callable:
    """装饰 InferClient 的非流式请求方法

    契约：
    - InferClient._last_retry_count：重试循环末尾需设置为 attempt
    - model 参数位于第 2 个位置参数或 keyword 参数
    """

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        model = kwargs.get("model", "") or (args[1] if len(args) > 1 else "")
        t0 = time.perf_counter()
        error = None
        retry_count = 0
        try:
            result = await func(self, *args, **kwargs)
            retry_count = getattr(self, "_last_retry_count", 0)
            return result
        except Exception as exc:
            error = exc
            retry_count = getattr(self, "_last_retry_count", 0)
            raise
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000
            emit(
                EVENT_INFERENCE_COMPLETED,
                model=model,
                duration_ms=duration_ms,
                retry_count=retry_count,
                error=error,
                error_type=classify_infer_error(error) if error else None,
            )

    return wrapper


def observe_inference_stream(func: Callable) -> Callable:
    """装饰 InferClient 的流式请求方法（async generator）

    契约：
    - InferClient._last_retry_count：重试循环末尾需设置为 attempt
    """

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        model = kwargs.get("model", "") or (args[1] if len(args) > 1 else "")
        t0 = time.perf_counter()
        error = None
        try:
            async for chunk in func(self, *args, **kwargs):
                yield chunk
        except Exception as exc:
            error = exc
            raise
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000
            retry_count = getattr(self, "_last_retry_count", 0)
            emit(
                EVENT_INFERENCE_COMPLETED,
                model=model,
                duration_ms=duration_ms,
                retry_count=retry_count,
                error=error,
                error_type=classify_infer_error(error) if error else None,
            )

    return wrapper


def observe_model_lifecycle(action: str, model_type: str) -> Callable:
    """装饰 ProcessorManager 的 register/unregister 方法"""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            result = await func(self, *args, **kwargs)
            first_arg = args[0] if args else None
            if hasattr(first_arg, "model_name"):
                model = first_arg.model_name
                run_id = getattr(first_arg, "run_id", "")
            elif isinstance(first_arg, tuple):
                model = first_arg[1] if len(first_arg) > 1 else "unknown"
                run_id = first_arg[0] if len(first_arg) > 0 else ""
            else:
                model = (
                    kwargs.get("model_name", str(first_arg) if first_arg else "unknown")
                )
                run_id = kwargs.get("run_id", "")
            emit(
                EVENT_MODEL_LIFECYCLE,
                action=action,
                model=model,
                run_id=run_id,
                model_type=model_type,
            )
            return result

        return wrapper

    return decorator