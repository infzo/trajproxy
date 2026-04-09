"""
Timing - 轻量级耗时追踪工具

用于追踪请求处理链路中各阶段的时间消耗。
支持嵌套计时，输出结构化的耗时报告。
"""

import time
from typing import Dict, Any, Optional, Union
from contextlib import contextmanager

from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


# NullTimer 使用的预分配空上下文管理器，避免每次 measure() 创建 generator
class _NullContextManager:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


_null_cm = _NullContextManager()


class StageTimer:
    """阶段计时器

    记录各阶段的开始时间、结束时间和耗时。
    线程安全：每个 ProcessContext 持有独立的 StageTimer 实例。
    """

    def __init__(self):
        self._records: Dict[str, Dict[str, Any]] = {}

    def start(self, stage: str):
        """记录阶段开始时间"""
        self._records[stage] = {
            "start": time.monotonic(),
            "end": None,
            "duration_ms": None
        }

    def end(self, stage: str) -> Optional[float]:
        """记录阶段结束时间，返回耗时(ms)"""
        if stage not in self._records:
            return None
        now = time.monotonic()
        rec = self._records[stage]
        rec["end"] = now
        rec["duration_ms"] = (now - rec["start"]) * 1000
        return rec["duration_ms"]

    @contextmanager
    def measure(self, stage: str):
        """上下文管理器：自动计时一个阶段"""
        self.start(stage)
        try:
            yield
        finally:
            self.end(stage)

    def get_duration(self, stage: str) -> Optional[float]:
        """获取阶段耗时(ms)"""
        rec = self._records.get(stage)
        return rec["duration_ms"] if rec else None

    def get_report(self) -> Dict[str, Any]:
        """生成耗时报告

        Returns:
            包含各阶段耗时的字典，以及首 token 时间（流式场景）
        """
        report = {}
        for stage, rec in self._records.items():
            report[stage] = rec["duration_ms"]
        return report

    def log_report(self, unique_id: str):
        """输出耗时报告到日志"""
        report = self.get_report()
        lines = [f"[{unique_id}] ===== 耗时分析报告 ====="]

        # 按关键阶段排序输出
        ordered_stages = [
            # 前处理阶段
            "route_receive", "context_create",
            "message_convert", "token_encode", "cache_lookup",
            "infer_connect",
            # 流式阶段
            "first_token", "stream_decode", "stream_parse",
            "stream_build_chunk",
            # 后处理阶段
            "finalize_stream", "response_build", "db_store",
        ]

        total_ms = 0.0
        for stage in ordered_stages:
            ms = report.get(stage)
            if ms is not None:
                lines.append(f"  {stage}: {ms:.2f}ms")
                total_ms += ms

        # 输出未在 ordered_stages 中的其他阶段
        for stage, ms in report.items():
            if stage not in ordered_stages and ms is not None:
                lines.append(f"  {stage}: {ms:.2f}ms")

        lines.append(f"  ===== 总计(已记录阶段): {total_ms:.2f}ms =====")
        lines.append(f"  ===== 耗时分析报告结束 =====")

        logger.info("\n".join(lines))

    def log_stream_chunk_timing(
        self,
        unique_id: str,
        chunk_index: int,
        decode_ms: float,
        parse_ms: float,
        build_ms: float
    ):
        """输出单个 stream chunk 的耗时（抽样输出，避免日志爆炸）"""
        # 只在特定 chunk 输出：第0个、第10个、第50个，之后每100个
        if chunk_index == 0 or chunk_index == 10 or chunk_index == 50 or chunk_index % 100 == 0:
            logger.info(
                f"[{unique_id}] chunk#{chunk_index} 耗时: "
                f"decode={decode_ms:.2f}ms, parse={parse_ms:.2f}ms, "
                f"build={build_ms:.2f}ms, total={decode_ms + parse_ms + build_ms:.2f}ms"
            )

    def get_elapsed_since(self, stage: str) -> Optional[float]:
        """获取从某阶段开始到现在的耗时(ms)"""
        rec = self._records.get(stage)
        if rec and rec["start"] is not None:
            return (time.monotonic() - rec["start"]) * 1000
        return None


class NullTimer:
    """空计时器 - 耗时追踪禁用时的替代品，所有方法为空操作，零开销"""

    def start(self, stage: str):
        pass

    def end(self, stage: str) -> Optional[float]:
        return None

    def measure(self, stage: str):
        """返回预分配的空上下文管理器，避免 generator 分配"""
        return _null_cm

    def get_duration(self, stage: str) -> Optional[float]:
        return None

    def get_report(self) -> Dict[str, Any]:
        return {}

    def log_report(self, unique_id: str):
        pass

    def log_stream_chunk_timing(
        self,
        unique_id: str,
        chunk_index: int,
        decode_ms: float,
        parse_ms: float,
        build_ms: float
    ):
        pass

    def get_elapsed_since(self, stage: str) -> Optional[float]:
        return None


def create_timer() -> Union[StageTimer, NullTimer]:
    """根据配置创建计时器实例

    Returns:
        timing 启用时返回 StageTimer，禁用时返回 NullTimer
    """
    from traj_proxy.utils.config import is_timing_enabled
    if is_timing_enabled():
        return StageTimer()
    return NullTimer()
