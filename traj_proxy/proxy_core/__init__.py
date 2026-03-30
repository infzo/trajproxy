"""
ProxyCore 模块 - LLM 代理核心处理模块

该模块提供以下主要组件：
- ProcessContext: 处理上下文数据类
- Processor: 主处理器，协调整个请求流程
- PromptBuilder: 消息到 PromptText 的转换器
- TokenBuilder: Token 处理器，支持前缀匹配缓存
- InferClient: Infer 服务客户端
- StreamingResponseGenerator: 流式响应生成器
"""

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.processor import Processor
from traj_proxy.proxy_core.prompt_builder import PromptBuilder
from traj_proxy.proxy_core.token_builder import TokenBuilder
from traj_proxy.proxy_core.infer_client import InferClient
from traj_proxy.proxy_core.processor_manager import ProcessorManager
from traj_proxy.proxy_core.streaming import StreamingResponseGenerator, format_sse
import traj_proxy.exceptions as exceptions_module

__all__ = [
    "ProcessContext",
    "Processor",
    "PromptBuilder",
    "TokenBuilder",
    "InferClient",
    "ProcessorManager",
    "StreamingResponseGenerator",
    "format_sse",
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
