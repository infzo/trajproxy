"""
API 数据模型定义

定义模型管理 API 的请求和响应模型。
"""

from typing import Optional
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
