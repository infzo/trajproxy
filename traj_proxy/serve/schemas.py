"""
API 数据模型定义

定义模型管理 API 的请求和响应模型。
"""

from typing import Optional, List, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from traj_proxy.utils.validators import validate_run_id, validate_model_name


# ========== 模型管理 API ==========

class RegisterModelRequest(BaseModel):
    """注册模型请求"""
    run_id: str = Field(default="", description="运行ID，空字符串表示全局模型")
    model_name: str = Field(..., description="模型名称")
    url: str = Field(..., description="Infer 服务 URL")
    api_key: str = Field(..., description="API 密钥")
    tokenizer_path: Optional[str] = Field(default=None, description="Tokenizer 路径（token_in_token_out=True 时必需）")
    token_in_token_out: bool = Field(default=False, description="是否使用 Token-in-Token-out 模式")
    tool_parser: str = Field(default="", description="Tool parser 名称")
    reasoning_parser: str = Field(default="", description="Reasoning parser 名称")

    @field_validator('run_id')
    @classmethod
    def validate_run_id_field(cls, v):
        """校验 run_id 格式"""
        valid, msg = validate_run_id(v)
        if not valid:
            raise ValueError(msg)
        return v

    @field_validator('model_name')
    @classmethod
    def validate_model_name_field(cls, v):
        """校验 model_name 格式"""
        valid, msg = validate_model_name(v)
        if not valid:
            raise ValueError(msg)
        return v

    @field_validator('tokenizer_path')
    @classmethod
    def validate_tokenizer_path(cls, v, info):
        """校验 tokenizer_path 在 token_in_token_out=True 时必须提供"""
        if info.data.get('token_in_token_out') and not v:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")
        return v


class RegisterModelResponse(BaseModel):
    """注册模型响应"""
    status: str
    run_id: str
    model_name: str
    detail: dict


class DeleteModelResponse(BaseModel):
    """删除模型响应"""
    status: str
    run_id: Optional[str]
    model_name: str
    deleted: bool


# ========== 轨迹查询 API ==========

class RecordMetadata(BaseModel):
    """Record 元数据（列表接口元素 / 详情接口的元数据部分）"""
    id: Optional[int] = None
    unique_id: str
    request_id: str
    session_id: str
    run_id: Optional[str] = None
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit_tokens: int = 0
    processing_duration_ms: Optional[float] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: Optional[datetime] = None
    error: Optional[str] = None
    archive_location: Optional[str] = None
    archived_at: Optional[datetime] = None


class RecordListResponse(BaseModel):
    """Record 列表响应"""
    session_id: str
    records: List[RecordMetadata]


class RecordDetail(RecordMetadata):
    """Record 详情（含详情字段，归档时为 None）"""
    tokenizer_path: Optional[str] = None
    messages: Optional[List[Any]] = None
    raw_request: Optional[Any] = None
    raw_response: Optional[Any] = None
    text_request: Optional[Any] = None
    text_response: Optional[Any] = None
    prompt_text: Optional[str] = None
    token_ids: Optional[List[int]] = None
    token_request: Optional[Any] = None
    token_response: Optional[Any] = None
    response_text: Optional[str] = None
    response_ids: Optional[List[int]] = None
    full_conversation_text: Optional[str] = None
    full_conversation_token_ids: Optional[List[int]] = None
    error_traceback: Optional[str] = None
