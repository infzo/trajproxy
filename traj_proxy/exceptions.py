"""
ProxyCore 模块自定义异常

定义了 ProxyCore 处理过程中可能出现的各种异常类型。
每个异常携带 status_code 和 error_type，便于统一转换为 HTTP 响应。
"""


class ProxyCoreError(Exception):
    """ProxyCore 基础异常

    所有 ProxyCore 模块异常的基类。
    子类通过覆盖 status_code 和 error_type 来自定义 HTTP 映射。
    """
    status_code: int = 500
    error_type: str = "internal_error"

    def __init__(self, message="", *, error_type=None, status_code=None):
        super().__init__(message)
        if error_type is not None:
            self.error_type = error_type
        if status_code is not None:
            self.status_code = status_code


class TokenizerNotFoundError(ProxyCoreError):
    """Tokenizer 未找到异常

    当指定的 tokenizer 路径不存在或加载失败时抛出。
    """
    status_code = 400
    error_type = "tokenizer_not_found"


class CacheError(ProxyCoreError):
    """缓存操作异常

    当缓存读取或写入操作失败时抛出。
    """
    status_code = 500
    error_type = "cache_error"


class InferServiceError(ProxyCoreError):
    """Infer 服务异常

    当 Infer 服务调用失败或返回错误响应时抛出。
    """
    status_code = 502
    error_type = "infer_service_error"


class InferTimeoutError(InferServiceError):
    """Infer 服务超时异常

    当连接或读取推理服务超时时抛出。
    """
    status_code = 504
    error_type = "infer_timeout"


class DatabaseError(ProxyCoreError):
    """数据库异常

    当数据库操作失败时抛出。
    """
    status_code = 503
    error_type = "database_error"


class SessionIdError(ProxyCoreError):
    """Session ID 格式错误异常

    当 Session ID 格式不符合规范或无法解析时抛出。

    规范格式: {run_id},{sample_id},{task_id}（逗号分隔）
    示例: my_run,sample_001,task_001
    """
    status_code = 422
    error_type = "invalid_session_id"
