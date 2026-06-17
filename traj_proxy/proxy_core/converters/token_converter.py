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
        # 与 vllm 行为对齐：保留特殊 token（如 <tool_call>、</tool_call>），
        # 确保 tool parser 文本检测正常工作。
        # hermes tool parser 依赖文本中的 <tool_call> 标记检测工具调用，
        # skip_special_tokens=True 会剔除这些标记。
        return self.tokenizer.decode(token_ids, skip_special_tokens=False)

    # 增量 decode 优化常量
    _DECODE_OVERLAP_WINDOW = 4   # 重叠窗口大小（UTF-8 最多 4 字节，覆盖 byte-fallback 边界）
    _DECODE_COMMIT_INTERVAL = 32 # 每累积多少 uncommitted token 时做一次全量重对齐

    def decode_streaming(
        self,
        token_ids: List[int],
        context: ProcessContext
    ) -> str:
        """流式解码 token IDs 为文本（增量优化版本）

        增量解码，处理 UTF-8 边界问题，确保不会在多字节字符中间截断。

        优化策略：维护 checkpoint（已全量 decode 对齐的 token 数量），
        两次 checkpoint 之间仅 decode overlap + uncommitted 部分（O(1)），
        避免每次 chunk 都全量 decode 导致的 O(n²) 复杂度。
        每 _DECODE_COMMIT_INTERVAL 个 uncommitted token 触发全量重对齐（O(n)），
        byte-fallback 边界异常时降级全量 decode（O(n)，罕见）。

        复杂度：O(n²) → O(n²/C) where C = _DECODE_COMMIT_INTERVAL (default 32)
            常数因子改善 ~3x
            ⚠️ byte-fallback tokenizer（如 LLaMA）在高频 emoji 输出时可能退化到全量 decode

        兼容 BPE / SentencePiece / byte-fallback 等不同 tokenizer：
        - overlap 窗口覆盖跨 token 边界的多字节字符
        - overlap 文本与独立 decode 不一致时（byte-fallback），降级全量 decode
        - _finalize_stream() 从 stream_buffer_ids 全量重解码，保证最终存储正确

        性能分析（32K token 响应，~3200 chunk）：
        - 原实现：3200 次 * 平均 16K token/次 ≈ 51M token 操作 (O(n²))
        - 优化后（O(n²/C), C=32）：~3200 次 * ~14 token/次 + ~100 次 * ~16K token/次 ≈ 16M
          实际更优，因为 checkpoint 推进后 overlap 不含全量前缀，多数 decode 仅处理 ~14 token

        Args:
            token_ids: 待解码的 token ID 列表（增量）
            context: 处理上下文

        Returns:
            本次可输出的文本（可能为空字符串）
        """
        # 追加到缓冲区
        context.stream_buffer_ids.extend(token_ids)

        total_len = len(context.stream_buffer_ids)
        checkpoint_len = context.stream_decode_checkpoint_len
        uncommitted = total_len - checkpoint_len

        # 首次 decode 或累积足够多 uncommitted token：全量 decode 重对齐
        # 对齐 vLLM SamplingParams 默认 skip_special_tokens=True
        # tokenizer 层面过滤特殊 token（含 EOS），客户端直接收到干净文本
        # Parser 用 delta_token_ids 检测边界，当 text 中标记被 strip 时返回 None（已兼容）
        # 存储由 _finalize_stream() 从 stream_buffer_ids 用 skip_special_tokens=False 重解码
        if checkpoint_len == 0 or uncommitted >= self._DECODE_COMMIT_INTERVAL:
            full_text = self.tokenizer.decode(
                context.stream_buffer_ids,
                skip_special_tokens=True
            )
            new_text = full_text[len(context.stream_buffer_text):]
            context.stream_buffer_text = full_text
            context.stream_decode_checkpoint_len = total_len
            return new_text

        # 增量 decode：仅 decode overlap + uncommitted 部分
        start = max(0, checkpoint_len - self._DECODE_OVERLAP_WINDOW)
        ids_to_decode = context.stream_buffer_ids[start:]
        segment_text = self.tokenizer.decode(
            ids_to_decode, skip_special_tokens=True
        )

        # 计算 overlap 部分的独立 decode 结果，用于定位新文本起始位置
        overlap_ids = context.stream_buffer_ids[start:checkpoint_len]
        overlap_text = self.tokenizer.decode(
            overlap_ids, skip_special_tokens=True
        )

        if segment_text.startswith(overlap_text):
            # 正常情况：overlap 部分与独立 decode 一致，可直接切片提取增量
            new_text = segment_text[len(overlap_text):]
            context.stream_buffer_text += new_text
            context.stream_decode_checkpoint_len = total_len
            return new_text

        # byte-fallback 边界异常：overlap 在上下文中与独立 decode 不一致
        # 降级为全量 decode 保证正确性（O(n) 但罕见，仅 byte-fallback tokenizer 在
        # 多字节字符跨越 checkpoint 边界时触发）
        logger.debug(
            f"decode_streaming: overlap 边界不一致，降级全量 decode "
            f"(overlap_len={len(overlap_ids)}, uncommitted={uncommitted})"
        )
        full_text = self.tokenizer.decode(
            context.stream_buffer_ids,
            skip_special_tokens=True
        )
        new_text = full_text[len(context.stream_buffer_text):]
        context.stream_buffer_text = full_text
        context.stream_decode_checkpoint_len = total_len
        return new_text
