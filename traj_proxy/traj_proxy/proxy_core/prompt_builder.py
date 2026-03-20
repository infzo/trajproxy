"""
PromptBuilder - 消息构建器

负责将 OpenAI Messages 转换为 PromptText，以及构建 OpenAI 格式的响应。
"""

from typing import List, Dict, Any, Optional
from transformers import AutoTokenizer

from traj_proxy.proxy_core.context import ProcessContext


class PromptBuilder:
    """消息构建器 - 负责将 OpenAI Messages 转换为 PromptText

    使用 transformers 的 AutoTokenizer.apply_chat_template() 方法
    将 OpenAI 格式的 messages 转换为模型特定的 prompt 格式。
    """

    def __init__(self, model: str, tokenizer_path: str):
        """初始化 PromptBuilder

        Args:
            model: 模型名称（用于响应中的 model 字段）
            tokenizer_path: Tokenizer 路径，用于加载模型特定的聊天模板
        """
        self.model = model
        self.tokenizer_path = tokenizer_path
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)

    async def build_prompt_text(
        self,
        messages: List[Dict[str, Any]],
        context: ProcessContext
    ) -> str:
        """将 OpenAI Messages 转换为 PromptText

        使用 tokenizer.apply_chat_template() 方法，
        将 OpenAI 格式的 messages 转换为模型特定的 prompt 格式。

        Args:
            messages: OpenAI Message 格式的消息列表
            context: 处理上下文

        Returns:
            模型特定的 prompt 文本
        """
        return self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

    def build_openai_response(
        self,
        response_text: str,
        context: ProcessContext,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """构建 OpenAI 格式的响应

        支持普通对话和工具调用场景。

        Args:
            response_text: 模型生成的响应文本
            context: 处理上下文，包含 infer_response 信息
            model: 可选的模型名称，默认使用初始化时指定的 model

        Returns:
            OpenAI 格式的响应字典
        """
        # 检查 infer_response 中是否有 tool_calls
        tool_calls = None
        finish_reason = "stop"
        if context.infer_response:
            choices = context.infer_response.get("choices", [])
            if choices:
                tool_calls = choices[0].get("tool_calls")
                finish_reason = choices[0].get("finish_reason", "stop")

        response_data = {
            "id": f"chatcmpl-{context.request_id}",
            "object": "chat.completion",
            "created": int(context.start_time.timestamp()) if context.start_time else 0,
            "model": model or self.model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text
                },
                "finish_reason": finish_reason
            }],
            "usage": {
                "prompt_tokens": context.prompt_tokens or 0,
                "completion_tokens": context.completion_tokens or 0,
                "total_tokens": context.total_tokens or 0
            }
        }

        # 处理 tool_calls（如果存在）
        if tool_calls:
            response_data["choices"][0]["message"]["tool_calls"] = tool_calls

        return response_data
