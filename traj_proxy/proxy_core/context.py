"""
ProcessContext - 请求处理上下文数据类

该数据类贯穿整个请求处理流程，包含输入数据、中间处理数据
和输出结果。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from traj_proxy.proxy_core.timing import StageTimer


@dataclass
class ProcessContext:
    """处理上下文，贯穿整个请求处理流程

    包含请求输入、三个阶段的中间结果、响应输出和元数据。

    数据流向：
    - 直接转发模式 (token_in_token_out=False):
      raw_request → 推理服务 → raw_response

    - Token模式 (token_in_token_out=True):
      raw_request → text_request → token_request → 推理服务 → token_response → text_response → raw_response
    """

    # ========== 基础标识 ==========
    request_id: str
    model: str
    session_id: Optional[str] = None  # 格式: app_id,sample_id,task_id
    unique_id: Optional[str] = None   # 格式: {session_id},{req_id}

    # ========== 阶段1: OpenAI Chat 格式 ==========
    # 便捷字段（保留用于快速访问）
    messages: List[Dict[str, Any]] = field(default_factory=list)
    request_params: Dict[str, Any] = field(default_factory=dict)

    # 完整请求/响应
    raw_request: Optional[Dict[str, Any]] = None   # 完整 OpenAI 请求
    raw_response: Optional[Dict[str, Any]] = None  # 完整 OpenAI 响应

    # ========== 阶段2: 文本推理格式（Token模式）==========
    # 仅 token_in_token_out=True 时使用
    text_request: Optional[Dict[str, Any]] = None   # 文本推理请求 {"prompt": "text...", ...}
    text_response: Optional[Dict[str, Any]] = None  # 文本推理响应

    # ========== 阶段3: Token 推理格式（Token模式）==========
    # 仅 token_in_token_out=True 时使用
    prompt_text: Optional[str] = None               # 文本 prompt
    token_ids: Optional[List[int]] = None           # Token ID 列表
    cached_token_ids: Optional[List[int]] = None    # 缓存命中的 token
    uncached_text: Optional[str] = None             # 未缓存文本
    uncached_token_ids: Optional[List[int]] = None  # 未缓存 token ids

    token_request: Optional[Dict[str, Any]] = None  # Token 推理请求 {"prompt": [1,2,3], ...}
    token_response: Optional[Dict[str, Any]] = None # Token 推理响应

    # ========== 输出数据 ==========
    response_text: Optional[str] = None             # 响应文本
    response_ids: Optional[List[int]] = None        # 响应 token ids

    # ========== 完整对话（用于前缀匹配）==========
    full_conversation_text: Optional[str] = None
    full_conversation_token_ids: Optional[List[int]] = None

    # ========== 元数据 ==========
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    processing_duration_ms: Optional[float] = None

    # ========== 统计信息 ==========
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit_tokens: Optional[int] = None      # 缓存命中的 token 数量

    # ========== 错误信息 ==========
    error: Optional[str] = None
    error_traceback: Optional[str] = None

    # ========== 流式处理状态 ==========
    is_stream: bool = False                    # 是否流式请求
    stream_buffer_text: str = ""               # 流式累积的响应文本
    stream_buffer_ids: List[int] = field(default_factory=list)  # 流式累积的 token ids
    stream_chunk_count: int = 0                # 已发送的 chunk 数量
    stream_finished: bool = False              # 流式是否结束

    # 流式累积字段（用于构建完整响应）
    stream_role: Optional[str] = None          # delta.role
    stream_reasoning: str = ""                 # delta.reasoning（vLLM 扩展）
    stream_tool_calls: Optional[List[Dict[str, Any]]] = None    # 流式累积的 tool_calls
    stream_function_call: Optional[Dict[str, Any]] = None       # 旧版 function_call
    stream_finish_reason: Optional[str] = None # 结束原因
    stream_logprobs: Optional[Dict[str, Any]] = None            # logprobs
    stream_stop_reason: Optional[Any] = None   # vLLM 扩展字段
    stream_token_ids: Optional[List[int]] = None                # vLLM 扩展字段

    # ========== 耗时追踪 ==========
    timer: Optional["StageTimer"] = None                        # 阶段计时器
