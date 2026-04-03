"""
TokenBuilder - Token 处理器

负责 Text ↔ TokenIds 转换和前缀匹配缓存策略。
"""

from typing import List, Optional, Dict, Any
from transformers import AutoTokenizer

from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.store.request_repository import RequestRepository


class TokenBuilder:
    """Token 处理器 - 负责 Text ↔ TokenIds 转换和缓存

    前缀匹配策略：
    1. 根据 session_id 查询数据库获取该会话的所有历史请求
    2. 用当前完整对话文本（请求+响应）与历史完整对话文本进行前缀匹配
    3. 匹配部分使用缓存的完整对话 token_ids
    4. 未匹配部分使用 tokenizer 编码
    5. 拼接得到完整的 token_ids
    """

    def __init__(self, model: str, tokenizer_path: str, request_repository: RequestRepository = None):
        """初始化 TokenBuilder

        Args:
            model: 模型名称
            tokenizer_path: Tokenizer 路径
            request_repository: 请求记录仓库，用于查询历史请求进行前缀匹配
        """
        self.model = model
        self.tokenizer_path = tokenizer_path
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
        self.request_repository = request_repository

    async def encode_text(
        self,
        text: str,
        context: ProcessContext
    ) -> List[int]:
        """将文本编码为 token IDs

        使用前缀匹配策略：
        1. 如果有 session_id，查询数据库获取历史请求
        2. 找到最长前缀匹配
        3. 拼接缓存和新生成的 token_ids

        Args:
            text: 待编码的文本
            context: 处理上下文

        Returns:
            token ID 列表
        """
        context.uncached_text = text
        context.cached_token_ids = []

        # 如果没有 session_id 或 request_repository，直接编码
        if not context.session_id or not self.request_repository:
            token_ids = self.tokenizer.encode(text, add_special_tokens=False)
            context.uncached_token_ids = token_ids
            return token_ids

        # 查询该 session 的所有历史请求
        history = await self.request_repository.get_by_session(context.session_id)

        # 找到最长前缀匹配（匹配完整对话文本）
        matched_trajectory = self._find_longest_prefix_match(text, history)

        if matched_trajectory:
            # 使用缓存的完整对话 token_ids
            cached_tokens = matched_trajectory.get("full_conversation_token_ids")
            cached_text = matched_trajectory.get("full_conversation_text", "")

            # 缓存命中的 token 数量
            context.cache_hit_tokens = len(cached_tokens) if cached_tokens else 0
            context.cached_token_ids = cached_tokens or []

            # 未匹配的文本
            uncached_text = text[len(cached_text):]
            context.uncached_text = uncached_text

            # 编码未匹配的部分
            if uncached_text:
                uncached_tokens = self.tokenizer.encode(uncached_text, add_special_tokens=False)
                context.uncached_token_ids = uncached_tokens
                return (cached_tokens or []) + uncached_tokens
            else:
                context.uncached_token_ids = []
                return cached_tokens or []
        else:
            # 没有匹配，直接编码
            token_ids = self.tokenizer.encode(text, add_special_tokens=False)
            context.uncached_token_ids = token_ids
            return token_ids

    def _find_longest_prefix_match(
        self,
        text: str,
        history: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """在历史记录中找到最长前缀匹配

        Args:
            text: 当前完整对话文本
            history: 历史请求记录列表

        Returns:
            匹配最长的轨迹记录，如果没有匹配则返回 None
        """
        longest_match = None
        longest_length = 0

        for trajectory in history:
            # 匹配完整对话文本（请求+响应）
            cached_text = trajectory.get("full_conversation_text")
            # 跳过无效记录（None 或空字符串）
            if not cached_text:
                continue
            if text.startswith(cached_text) and len(cached_text) > longest_length:
                longest_match = trajectory
                longest_length = len(cached_text)

        return longest_match

    async def decode_tokens(
        self,
        token_ids: List[int],
        context: ProcessContext
    ) -> str:
        """将 token IDs 解码为文本

        Args:
            token_ids: token ID 列表
            context: 处理上下文

        Returns:
            解码后的文本
        """
        return self.tokenizer.decode(token_ids, skip_special_tokens=False)

    def decode_tokens_streaming(
        self,
        token_ids: List[int],
        context: ProcessContext
    ) -> str:
        """流式解码 token IDs 为文本

        增量解码，处理 UTF-8 边界问题，确保不会在多字节字符中间截断。

        Args:
            token_ids: 待解码的 token ID 列表（增量）
            context: 处理上下文

        Returns:
            本次可输出的文本（可能为空字符串）
        """
        # 追加到缓冲区
        context.stream_buffer_ids.extend(token_ids)

        # 尝试解码整个缓冲区
        full_text = self.tokenizer.decode(
            context.stream_buffer_ids,
            skip_special_tokens=False
        )

        # 计算新增的文本
        new_text = full_text[len(context.stream_buffer_text):]
        context.stream_buffer_text = full_text

        return new_text
