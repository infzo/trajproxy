"""
统一错误响应处理

将内部异常转换为结构化的 HTTP 错误响应，使用户能快速定位问题。
"""

from fastapi import HTTPException

from traj_proxy.exceptions import ProxyCoreError


def build_error_response(request_id: str, exc: Exception) -> tuple:
    """将异常转换为用户友好的错误响应

    Args:
        request_id: 请求 ID，用于关联日志
        exc: 捕获的异常

    Returns:
        (error_body, status_code) 元组
        error_body 可直接用作 HTTPException 的 detail
    """
    if isinstance(exc, HTTPException):
        # HTTPException 已经是格式化的错误，直接透传
        return exc.detail, exc.status_code

    if isinstance(exc, ProxyCoreError):
        return {
            "type": exc.error_type,
            "message": str(exc),
            "request_id": request_id,
        }, exc.status_code

    # 未知异常：不暴露内部细节
    return {
        "type": "internal_error",
        "message": "内部服务错误",
        "request_id": request_id,
    }, 500
