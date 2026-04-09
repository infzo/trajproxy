"""
PrefixMatchCache - 前缀匹配缓存策略

通过匹配历史对话的前缀来优化 Token 编码。
"""

from typing import List, Optional, Dict, Any, TYPE_CHECKING

from traj_proxy.proxy_core.cache.base import BaseCacheStrategy
from traj_proxy.utils.logger import get_logger

if TYPE_CHECKING:
    from traj_proxy.proxy_core.context import ProcessContext
    from traj_proxy.store.request_repository import RequestRepository

logger = get_logger(__name__)


class PrefixMatchCache(BaseCacheStrategy):
    """前缀匹配缓存策略

    优化策略：
    1. 根据 session_id 查询数据库获取该会话的所有历史请求
    2. 用当前完整对话文本（请求+响应）与历史完整对话文本进行前缀匹配
    3. 匹配部分使用缓存的完整对话 token_ids
    4. 未匹配部分使用 tokenizer 编码
    5. 拼接得到完整的 token_ids
    """

    def __init__(self, request_repository: "RequestRepository"):
        """初始化 PrefixMatchCache

        Args:
            request_repository: 请求记录仓库，用于查询历史请求
        """
        self.request_repository = request_repository

    async def encode_with_cache(
        self,
        text: str,
        context: "ProcessContext",
        tokenizer
    ) -> List[int]:
        """使用前缀匹配缓存优化文本编码

        Args:
            text: 待编码的文本
            context: 处理上下文
            tokenizer: tokenizer 实例

        Returns:
            token ID 列表
        """
        # 初始化上下文缓存信息
        context.uncached_text = text
        context.cached_token_ids = []

        # 如果没有 session_id 或 request_repository，直接编码
        if not context.session_id or not self.request_repository:
            logger.debug(
                f"[{context.unique_id}] 缓存跳过: "
                f"session_id={'有' if context.session_id else '无'}, "
                f"repo={'有' if self.request_repository else '无'}"
            )
            token_ids = tokenizer.encode(text, add_special_tokens=False)
            context.uncached_token_ids = token_ids
            return token_ids

        # 查询该 session 的所有历史请求
        history = await self.request_repository.get_by_session(context.session_id)
        logger.debug(
            f"[{context.unique_id}] 缓存查找: "
            f"session_id={context.session_id}, "
            f"history_count={len(history)}, "
            f"current_text_length={len(text)}"
        )

        # 找到最长前缀匹配（匹配完整对话文本）
        matched_trajectory = self._find_longest_prefix_match(text, history)

        if matched_trajectory:
            # 使用缓存的完整对话 token_ids
            cached_tokens = matched_trajectory.get("full_conversation_token_ids")
            cached_text = matched_trajectory.get("full_conversation_text", "")

            logger.info(
                f"[{context.unique_id}] 缓存命中: "
                f"cached_text_length={len(cached_text) if cached_text else 0}, "
                f"cached_tokens={len(cached_tokens) if cached_tokens else 0}"
            )

            # 缓存命中的 token 数量
            context.cache_hit_tokens = len(cached_tokens) if cached_tokens else 0
            context.cached_token_ids = cached_tokens or []

            # 未匹配的文本
            uncached_text = text[len(cached_text):]
            context.uncached_text = uncached_text

            # 编码未匹配的部分
            if uncached_text:
                uncached_tokens = tokenizer.encode(uncached_text, add_special_tokens=False)
                context.uncached_token_ids = uncached_tokens
                return (cached_tokens or []) + uncached_tokens
            else:
                context.uncached_token_ids = []
                return cached_tokens or []
        else:
            # 没有匹配，直接编码
            if history:
                # 调试：输出为什么没匹配
                for i, t in enumerate(history):
                    ct = t.get("full_conversation_text", "")
                    logger.debug(
                        f"[{context.unique_id}] 缓存未命中 history[{i}]: "
                        f"conv_text_len={len(ct) if ct else 0}, "
                        f"has_token_ids={'full_conversation_token_ids' in t}, "
                        f"match={'prefix_match' if ct and text.startswith(ct) else 'no_match'}"
                    )
            token_ids = tokenizer.encode(text, add_special_tokens=False)
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
