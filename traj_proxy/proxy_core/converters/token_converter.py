"""
TokenConverter - Token 转换器

负责 Text ↔ TokenIds 转换，支持缓存策略。
"""

from typing import List, Optional, TYPE_CHECKING

from traj_proxy.proxy_core.converters.base import BaseConverter
from traj_proxy.proxy_core.context import ProcessContext
from traj_proxy.utils.logger import get_logger

if TYPE_CHECKING:
    from traj_proxy.proxy_core.cache.base import BaseCacheStrategy

logger = get_logger(__name__)


class TokenConverter(BaseConverter):
    """Token 转换器 - 负责 Text ↔ TokenIds 转换

    支持通过缓存策略优化编码过程（如前缀匹配缓存）。
    """

    def __init__(
        self,
        tokenizer,
        cache_strategy: Optional["BaseCacheStrategy"] = None
    ):
        """初始化 TokenConverter

        Args:
            tokenizer: transformers.PreTrainedTokenizerBase 实例
            cache_strategy: 可选的缓存策略（如 PrefixMatchCache）
        """
        self.tokenizer = tokenizer
        self.cache_strategy = cache_strategy

    async def convert(self, text: str, context: ProcessContext) -> List[int]:
        """转换文本为 token IDs（encode 的别名）

        Args:
            text: 待编码的文本
            context: 处理上下文

        Returns:
            token ID 列表
        """
        return await self.encode(text, context)

    async def encode(
        self,
        text: str,
        context: ProcessContext
    ) -> List[int]:
        """将文本编码为 token IDs

        如果配置了缓存策略，使用缓存优化编码过程。

        Args:
            text: 待编码的文本
            context: 处理上下文

        Returns:
            token ID 列表
        """
        if self.cache_strategy:
            with context.timer.measure("cache_lookup"):
                return await self.cache_strategy.encode_with_cache(text, context, self.tokenizer)

        # 无缓存策略，直接编码
        context.uncached_text = text
        context.cached_token_ids = []
        token_ids = self.tokenizer.encode(text, add_special_tokens=False)
        context.uncached_token_ids = token_ids
        return token_ids

    async def decode(
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

    def decode_streaming(
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
