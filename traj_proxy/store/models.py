"""
数据模型定义

所有数据库表对应的 dataclass 数据模型。
"""

from dataclasses import dataclass
from typing import List, Optional, Any
from datetime import datetime


@dataclass
class ModelConfig:
    """模型配置数据类

    对应 model_registry 表。
    """
    url: str
    api_key: str
    tokenizer_path: Optional[str] = None  # 可选，直接转发模式下不需要
    run_id: str = ""  # 运行ID，空字符串表示全局模型
    model_name: str = ""
    token_in_token_out: bool = False
    tool_parser: str = ""  # 工具解析器名称
    reasoning_parser: str = ""  # 推理解析器名称
    updated_at: Optional[datetime] = None


@dataclass
class RequestRecord:
    """请求轨迹记录数据类

    对应 request_records 表。
    """
    unique_id: str
    request_id: str
    session_id: str
    model: str
    messages: List[Any]
    tokenizer_path: Optional[str] = None  # 可选，直接转发模式下不需要

    # 阶段1: OpenAI Chat 格式
    raw_request: Optional[Any] = None      # 完整 OpenAI 请求
    raw_response: Optional[Any] = None     # 完整 OpenAI 响应

    # 阶段2: 文本推理格式（Token模式）
    text_request: Optional[Any] = None     # 文本推理请求
    text_response: Optional[Any] = None    # 文本推理响应

    # 阶段3: Token 推理格式（Token模式）
    prompt_text: Optional[str] = None      # 文本 prompt
    token_ids: Optional[List[int]] = None  # Token ID 列表
    token_request: Optional[Any] = None    # Token 推理请求
    token_response: Optional[Any] = None   # Token 推理响应

    # 输出数据
    response_text: Optional[str] = None
    response_ids: Optional[List[int]] = None

    # 完整对话
    full_conversation_text: Optional[str] = None
    full_conversation_token_ids: Optional[List[int]] = None

    # 元数据
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    processing_duration_ms: Optional[float] = None

    # 统计信息
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit_tokens: int = 0

    # 错误信息
    error: Optional[str] = None
    error_traceback: Optional[str] = None

    # 创建时间
    created_at: Optional[datetime] = None
