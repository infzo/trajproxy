"""
请求结果归因 — 纯函数，仅依赖 ProcessContext + 异常类型

业务代码（Pipeline/Processor/Routes）完全不需要知道 outcome 的存在。
此函数由 EventBus 订阅者调用，不改变业务执行路径。
"""

from typing import Optional


def determine_outcome(context, exception: Optional[Exception] = None) -> str:
    """从 ProcessContext 状态 + 异常类型 推导 outcome

    outcome 枚举（8 种）：
    - success: 正常完成
    - stream_partial: 流式中途崩溃
    - error_client: 客户端错误（HTTP 4xx）
    - error_server: TrajProxy 内部错误
    - error_infer: 推理服务错误
    - error_database: 数据库错误
    - error_rate_limit: 并发限流（429）
    - timeout: 推理超时
    """
    from traj_proxy.exceptions import (
        DatabaseError,
        InferServiceError,
        InferTimeoutError,
        ParserError,
    )

    # ── 异常驱动 ──
    # 注意：InferTimeoutError 继承自 InferServiceError，
    # 因此 isinstance 判断顺序至关重要：必须先匹配子类。
    if exception is not None:
        if isinstance(exception, InferTimeoutError):
            return "timeout"
        if isinstance(exception, InferServiceError):
            return "error_infer"
        if isinstance(exception, DatabaseError):
            return "error_database"
        if isinstance(exception, ParserError):
            return "error_parser"
        status = getattr(exception, "status_code", None)
        if status == 429:
            return "error_rate_limit"
        if status and 400 <= status < 500:
            return "error_client"
        return "error_server"

    # ── 流式请求特殊判定（HTTP 200 但不一定"成功"）──
    if getattr(context, "is_stream", False):
        if getattr(context, "stream_finished", True):
            return "success"
        if getattr(context, "stream_chunk_count", 0) > 0:
            return "stream_partial"
        return "error_server"

    # ── 非流式：有 error 字段但无异常 ──
    if getattr(context, "error", None):
        return "error_server"

    return "success"