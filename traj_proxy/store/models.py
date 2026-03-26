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
    job_id: str = ""  # 作业ID，空字符串表示全局模型
    model_name: str = ""
    url: str
    api_key: str
    tokenizer_path: str
    token_in_token_out: bool = False
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
    tokenizer_path: str
    messages: List[Any]
    prompt_text: str
    token_ids: List[int]
    full_conversation_text: Optional[str] = None
    full_conversation_token_ids: Optional[List[int]] = None
    response: Optional[Any] = None
    response_text: Optional[str] = None
    response_ids: Optional[List[int]] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    processing_duration_ms: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit_tokens: int = 0
    error: Optional[str] = None
    error_traceback: Optional[str] = None
    created_at: Optional[datetime] = None
