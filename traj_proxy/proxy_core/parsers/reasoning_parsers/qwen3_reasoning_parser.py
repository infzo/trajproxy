"""
Qwen3 Reasoning Parser

支持格式：
<thinky>推理过程</thinke>正常回复

注意：Qwen3 需要同时存在 start 和 end token 才会解析
"""

from typing import Optional, Any

from .basic_parsers import BaseThinkingReasoningParser


class Qwen3ReasoningParser(BaseThinkingReasoningParser):
    """
    Qwen3 推理内容解析器

    Qwen3 使用 <thinky></thinke> 标记包围推理内容。
    与 DeepSeek R1 不同，Qwen3 要求同时存在 start 和 end token。
    """

    @property
    def start_token(self) -> str:
        """推理开始标记"""
        return "<thinky>"

    @property
    def end_token(self) -> str:
        """推理结束标记"""
        return "</thinke>"

    def extract_reasoning(
        self,
        model_output: str,
        request: Optional[Any] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """
        非流式解析推理内容

        Qwen3 要求更严格：需要同时存在 start_token 和 end_token。
        如果只存在其中一个，则不解析，返回原内容。

        对于文本 <thinky>abc</thinke>xyz:
        - 'abc' 作为 reasoning
        - 'xyz' 作为 content
        """
        # 检查是否同时存在 start 和 end token
        if self.start_token not in model_output or self.end_token not in model_output:
            return None, model_output

        # 移除 start_token 之前的内容
        parts = model_output.partition(self.start_token)
        model_output = parts[2] if parts[1] else parts[0]

        # 再次检查 end_token
        if self.end_token not in model_output:
            return None, model_output

        # 分割 reasoning 和 content
        reasoning, _, content = model_output.partition(self.end_token)

        return reasoning if reasoning else None, content if content else None
