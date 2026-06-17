# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
Responses 协议类适配器

提供 ResponsesRequest、ResponseTextConfig 等。
"""
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, Literal


@dataclass
class ResponseFormatTextJSONSchemaConfig:
    """响应格式 JSON Schema 配置"""
    name: str
    schema: Optional[Dict[str, Any]] = None
    type: Literal["json_schema"] = "json_schema"
    description: Optional[str] = None
    strict: Optional[bool] = None


@dataclass
class ResponseTextConfig:
    """响应文本配置"""
    format: Optional[ResponseFormatTextJSONSchemaConfig] = None


@dataclass
class ResponsesRequest:
    """Responses 请求（简化版）"""
    text: Optional[ResponseTextConfig] = None

    def __post_init__(self):
        if self.text is None:
            self.text = ResponseTextConfig()
