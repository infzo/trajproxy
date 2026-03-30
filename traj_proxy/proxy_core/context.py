"""
ProcessContext - 请求处理上下文数据类

该数据类贯穿整个请求处理流程，包含输入数据、中间处理数据
和输出结果。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict
from datetime import datetime


@dataclass
class ProcessContext:
    """处理上下文，贯穿整个请求处理流程

    包含请求输入、中间处理结果、响应输出和元数据。
    """

    # 输入数据
    request_id: str
    model: str
    messages: List[Dict[str, Any]]  # OpenAI Message 格式
    request_params: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None  # 格式: app_id;sample_id;task_id
    unique_id: Optional[str] = None   # 格式: {session_id};{req_id}

    # 中间数据 - Prompt 相关
    prompt_text: Optional[str] = None

    # 中间数据 - Token 相关
    token_ids: Optional[List[int]] = None
    cached_token_ids: Optional[List[int]] = None   # 缓存匹配部分的 token_ids
    uncached_text: Optional[str] = None           # 未匹配的文本
    uncached_token_ids: Optional[List[int]] = None # 未匹配部分的 token_ids

    # 中间数据 - Infer 响应
    infer_response: Optional[Dict[str, Any]] = None
    infer_request_body: Optional[Dict[str, Any]] = None

    # 输出数据
    response: Optional[Dict[str, Any]] = None  # OpenAI Response 格式
    response_text: Optional[str] = None
    response_ids: Optional[List[int]] = None

    # 完整对话（请求+响应）用于前缀匹配
    full_conversation_text: Optional[str] = None    # 完整对话文本
    full_conversation_token_ids: Optional[List[int]] = None  # 完整对话 token_ids

    # 元数据
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    processing_duration_ms: Optional[float] = None

    # 统计信息
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit_tokens: Optional[int] = None      # 缓存命中的 token 数量

    # 错误信息
    error: Optional[str] = None
    error_traceback: Optional[str] = None

    # 流式处理状态
    is_stream: bool = False                    # 是否流式请求
    stream_buffer_text: str = ""               # 流式累积的响应文本
    stream_buffer_ids: List[int] = field(default_factory=list)  # 流式累积的 token ids
    stream_chunk_count: int = 0                # 已发送的 chunk 数量
    stream_finished: bool = False              # 流式是否结束
    stream_tool_calls: Optional[List[Dict[str, Any]]] = None    # 流式累积的 tool_calls
