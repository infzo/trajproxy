"""
ProxyCore 模块自定义异常

定义了 ProxyCore 处理过程中可能出现的各种异常类型。
"""


class ProxyCoreError(Exception):
    """ProxyCore 基础异常

    所有 ProxyCore 模块异常的基类。
    """
    pass


class TokenizerNotFoundError(ProxyCoreError):
    """Tokenizer 未找到异常

    当指定的 tokenizer 路径不存在或加载失败时抛出。
    """
    pass


class CacheError(ProxyCoreError):
    """缓存操作异常

    当缓存读取或写入操作失败时抛出。
    """
    pass


class InferServiceError(ProxyCoreError):
    """Infer 服务异常

    当 Infer 服务调用失败或返回错误响应时抛出。
    """
    pass


class DatabaseError(ProxyCoreError):
    """数据库异常

    当数据库操作失败时抛出。
    """
    pass


class SessionIdError(ProxyCoreError):
    """Session ID 格式错误异常

    当 Session ID 格式不符合规范或无法解析时抛出。

    规范格式: app_id;sample_id;task_id
    """
    pass
