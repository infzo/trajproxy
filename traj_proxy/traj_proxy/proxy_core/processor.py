"""
Processor - 主处理器

协调整个 LLM 请求处理流程的核心类。
"""

from typing import Optional
from datetime import datetime
import traceback

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.proxy_core.prompt_builder import PromptBuilder
from traj_proxy.proxy_core.token_builder import TokenBuilder
from traj_proxy.exceptions import DatabaseError

from traj_proxy.utils.logger import get_logger
logger = get_logger(__name__)

class Processor:
    """主处理器 - 协调整个 LLM 请求处理流程

    处理流程：
    1. Message → PromptText 转换（使用 tokenizer.apply_chat_template）
    2. PromptText → TokenIds 转换（使用前缀匹配完整对话）
    3. 向 Infer 发送 TextCompletions 请求
    4. Response → ResponseText
    5. 构建完整对话（请求+响应）用于前缀匹配
    6. 统计信息
    7. 构建 OpenAI Response 响应（支持 tool_calls）
    8. 存储完整请求到数据库
    """

    def __init__(
        self,
        model: str,
        tokenizer_path: str,
        db_manager=None,
        infer_client=None
    ):
        """初始化 Processor

        Args:
            model: 模型名称
            tokenizer_path: Tokenizer 路径
            db_manager: 数据库管理器，用于存储轨迹记录
            infer_client: Infer 服务客户端
        """
        self.model = model
        self.tokenizer_path = tokenizer_path
        self.db_manager = db_manager
        self.infer_client = infer_client

        # 初始化构建器
        self.prompt_builder = PromptBuilder(model, tokenizer_path)
        self.token_builder = TokenBuilder(model, tokenizer_path, db_manager)

    async def process_request(
        self,
        messages: list,
        request_id: str,
        session_id: Optional[str] = None,
        **request_params
    ) -> ProcessContext:
        """处理完整的 LLM 请求

        Args:
            messages: OpenAI 格式的消息列表
            request_id: 请求 ID
            session_id: 会话 ID（格式: app_id#sample_id#task_id）
            **request_params: 请求参数（如 max_tokens, temperature 等）

        Returns:
            处理上下文，包含完整的处理结果和响应

        Raises:
            各种异常，包括 DatabaseError、InferServiceError 等
        """
        # 构建 unique_id
        unique_id = f"{session_id}#{request_id}" if session_id else request_id

        # 初始化上下文
        context = ProcessContext(
            request_id=request_id,
            model=self.model,
            messages=messages,
            request_params=request_params,
            session_id=session_id,
            unique_id=unique_id
        )
        context.start_time = datetime.now()

        try:
            # 1. Message → PromptText 转换（使用 tokenizer.apply_chat_template）
            context.prompt_text = await self.prompt_builder.build_prompt_text(
                messages, context
            )

            # 2. PromptText → TokenIds 转换（使用前缀匹配完整对话）
            context.token_ids = await self.token_builder.encode_text(
                context.prompt_text, context
            )
            context.prompt_tokens = len(context.token_ids)

            # 3. 向 Infer 发送 TextCompletions 请求
            context.infer_request_body = {
                "prompt": context.prompt_text,
                "model": self.model,
                **request_params
            }
            context.infer_response = await self.infer_client.send_text_completion(
                prompt_text=context.prompt_text,
                model=self.model,
                **request_params
            )
            print(f"文本推理对话: infer_request_body={context.infer_request_body}, infer_response={context.infer_response}")

            # 4. Response → ResponseText
            if "choices" in context.infer_response and context.infer_response["choices"]:
                choice = context.infer_response["choices"][0]
                context.response_text = choice.get("text", "")

            # 5. 构建完整对话（请求+响应）用于前缀匹配
            context.full_conversation_text = context.prompt_text + context.response_text

            # 构建完整对话 token_ids（请求 + 响应）
            if context.response_text:
                response_token_ids = self.token_builder.tokenizer.encode(
                    context.response_text,
                    add_special_tokens=False
                )
                context.full_conversation_token_ids = context.token_ids + response_token_ids
                context.response_ids = response_token_ids
            else:
                context.full_conversation_token_ids = context.token_ids

            # 6. 统计信息
            if "usage" in context.infer_response:
                usage = context.infer_response["usage"]
                context.completion_tokens = usage.get("completion_tokens", 0)
                context.total_tokens = usage.get("total_tokens", 0)
            else:
                # 从响应文本估算
                context.completion_tokens = len(context.response_ids or [])
                context.total_tokens = context.prompt_tokens + context.completion_tokens

            # 7. 构建 OpenAI Response 响应（支持 tool_calls）
            context.response = self.prompt_builder.build_openai_response(
                context.response_text, context
            )

            context.end_time = datetime.now()
            context.processing_duration_ms = (
                context.end_time - context.start_time
            ).total_seconds() * 1000

            # 8. 存储完整请求到数据库
            if self.db_manager:
                try:
                    await self.db_manager.insert_trajectory(context, self.tokenizer_path)
                except DatabaseError as e:
                    # 存储失败不影响主流程，记录错误即可
                    context.error = f"存储轨迹失败: {str(e)}"

            return context

        except Exception as e:
            context.error = str(e)
            context.error_traceback = traceback.format_exc()
            context.end_time = datetime.now()
            # 即使出错也尝试存储到数据库
            if self.db_manager:
                try:
                    await self.db_manager.insert_trajectory(context, self.tokenizer_path)
                except DatabaseError:
                    # 忽略存储错误，避免掩盖原始错误
                    pass
            raise
