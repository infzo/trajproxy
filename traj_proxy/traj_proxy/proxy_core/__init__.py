"""
ProxyCore 模块 - LLM 代理核心处理模块

该模块提供以下主要组件：
- ProcessContext: 处理上下文数据类
- Processor: 主处理器，协调整个请求流程
- PromptBuilder: 消息到 PromptText 的转换器
- TokenBuilder: Token 处理器，支持前缀匹配缓存
- InferClient: Infer 服务客户端
"""

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.processor import Processor
from traj_proxy.proxy_core.prompt_builder import PromptBuilder
from traj_proxy.proxy_core.token_builder import TokenBuilder
from traj_proxy.proxy_core.infer_client import InferClient
import traj_proxy.exceptions as exceptions_module

__all__ = [
    "ProcessContext",
    "Processor",
    "PromptBuilder",
    "TokenBuilder",
    "InferClient",
    "ProxyCoreError",
    "TokenizerNotFoundError",
    "CacheError",
    "InferServiceError",
    "DatabaseError",
    "SessionIdError",
]

# 从上层模块导入异常
ProxyCoreError = exceptions_module.ProxyCoreError
TokenizerNotFoundError = exceptions_module.TokenizerNotFoundError
CacheError = exceptions_module.CacheError
InferServiceError = exceptions_module.InferServiceError
DatabaseError = exceptions_module.DatabaseError
SessionIdError = exceptions_module.SessionIdError


def parse_and_validate_session_id(session_id: str) -> tuple[str, str, str]:
    """解析并验证 Session ID

    Args:
        session_id: 格式为 app_id#sample_id#task_id

    Returns:
        (app_id, sample_id, task_id)

    Raises:
        SessionIdError: 格式错误时抛出
    """
    parts = session_id.split('#')
    if len(parts) != 3:
        raise SessionIdError(
            f"Session ID 格式错误，应为 app_id#sample_id#task_id，实际为: {session_id}"
        )
    app_id, sample_id, task_id = parts
    if not app_id or not sample_id or not task_id:
        raise SessionIdError(
            f"Session ID 部分不能为空: {session_id}"
        )
    return app_id, sample_id, task_id
