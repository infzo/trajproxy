"""
TokenizerCache - 按 tokenizer_path 共享 tokenizer 实例

同一 tokenizer_path 只加载一次，通过引用计数管理生命周期。
Processor 创建时 acquire，淘汰/注销时 release，归零时清除缓存。
"""

from typing import Dict

from transformers import AutoTokenizer

from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class TokenizerCache:
    """按 tokenizer_path 缓存 tokenizer，引用计数管理生命周期"""

    def __init__(self):
        self._cache: Dict[str, AutoTokenizer] = {}
        self._ref_counts: Dict[str, int] = {}

    def get_or_load(self, tokenizer_path: str) -> AutoTokenizer:
        """获取或加载 tokenizer，引用计数 +1"""
        if tokenizer_path in self._cache:
            self._ref_counts[tokenizer_path] += 1
            logger.debug(
                f"Tokenizer 缓存命中: {tokenizer_path} "
                f"(refs={self._ref_counts[tokenizer_path]})"
            )
            return self._cache[tokenizer_path]

        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_path, trust_remote_code=True
        )
        self._cache[tokenizer_path] = tokenizer
        self._ref_counts[tokenizer_path] = 1
        logger.info(f"Tokenizer 已加载并缓存: {tokenizer_path}")
        return tokenizer

    def release(self, tokenizer_path: str) -> None:
        """引用计数 -1，归零时清除缓存"""
        if tokenizer_path not in self._ref_counts:
            return

        self._ref_counts[tokenizer_path] -= 1
        if self._ref_counts[tokenizer_path] <= 0:
            del self._cache[tokenizer_path]
            del self._ref_counts[tokenizer_path]
            logger.info(f"Tokenizer 引用归零，已释放: {tokenizer_path}")
        else:
            logger.debug(
                f"Tokenizer 引用递减: {tokenizer_path} "
                f"(refs={self._ref_counts[tokenizer_path]})"
            )

    @property
    def size(self) -> int:
        return len(self._cache)
