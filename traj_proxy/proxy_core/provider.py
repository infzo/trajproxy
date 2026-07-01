"""
TrajectoryProvider - 转录提供者

负责处理轨迹记录查询的业务逻辑，以及 route_experts 大字段卸载后的
fallback 回拉业务逻辑（设计文档 docs/design/features/route-experts-offload.md 九）。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from traj_proxy.store.blob_storage import BlobStorage, BlobStorageError, StreamHandle
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.store.r3_ref_repository import R3RefRepository
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


# ========== route_experts fallback 回拉结果类型 ==========
# 标记联合，路由层穷尽匹配后映射为 HTTP 响应；行为矩阵见设计文档 九.3。


@dataclass(frozen=True, slots=True)
class RouteExpertsNotFound:
    """404: route_experts 未卸载或字段缺失。

    触发场景: feature off（依赖未注入）、ref 行不存在、或状态异常。
    """

    reason: str = "route_experts not offloaded"
    hint: str = (
        "feature was off when stored, or field absent in original response"
    )


@dataclass(frozen=True, slots=True)
class RouteExpertsUploading:
    """202: blob 上传中，client 应退避重试。"""

    retry_after_seconds: int = 30


@dataclass(frozen=True, slots=True)
class RouteExpertsReady:
    """200: blob 就绪，可流式返回原始 route_experts JSON。

    stream_handle 持有已打开的 blob 流（open_stream 已完成认证/打开），
    路由层通过 iter_bytes 流式消费，finally 调 close 幂等释放资源。
    """

    blob_key: str
    stream_handle: StreamHandle


@dataclass(frozen=True, slots=True)
class RouteExpertsConsumed:
    """410: blob 已被消费（本期默认不标记 consumed，不会触发）。"""

    reason: str = "route_experts already consumed"


RouteExpertsFetchResult = Union[
    RouteExpertsNotFound,
    RouteExpertsUploading,
    RouteExpertsReady,
    RouteExpertsConsumed,
]
"""provider.get_route_experts() 返回值类型，路由层穷尽匹配后映射为 HTTP 响应。"""


class TrajectoryProvider:
    """转录提供者 - 处理轨迹记录查询业务逻辑

    封装数据库访问逻辑，为 routes 提供业务接口。
    route_experts fallback 回拉为可选能力：仅当 r3_ref_repository 与
    blob_storage 均被注入时启用，否则 get_route_experts 恒返回 404
    （等价于 feature off，符合设计文档行为矩阵）。
    """

    def __init__(
        self,
        request_repository: RequestRepository,
        r3_ref_repository: Optional[R3RefRepository] = None,
        blob_storage: Optional[BlobStorage] = None,
    ) -> None:
        """初始化 TrajectoryProvider

        Args:
            request_repository: 请求记录仓库（必填，现有轨迹查询依赖）。
            r3_ref_repository: route_experts 引用表仓库（可选，未注入则
                fallback 端点恒返回 404，等价于 feature off）。
            blob_storage: route_experts blob 存储（可选，同上）。
        """
        self.request_repository = request_repository
        self._r3_ref_repository = r3_ref_repository
        self._blob_storage = blob_storage

    async def get_trajectory(
        self,
        session_id: str,
        limit: int = 10000
    ) -> Dict[str, Any]:
        """根据 session_id 获取所有轨迹记录

        Args:
            session_id: 会话ID (格式: app_id,sample_id,task_id)
            limit: 最多返回的记录数，默认为100

        Returns:
            包含session_id、记录数量和记录列表的字典
        """
        records = await self.request_repository.get_by_session(session_id, limit)
        return {
            "session_id": session_id,
            "count": len(records),
            "records": records
        }

    async def list_trajectories(
        self,
        run_id: str
    ) -> Dict[str, Any]:
        """查询指定 run_id 下的轨迹列表

        Args:
            run_id: 运行ID（必填）

        Returns:
            包含 run_id 和轨迹列表的字典
        """
        sessions = await self.request_repository.list_sessions(run_id)
        return {
            "run_id": run_id,
            "trajectories": sessions
        }

    async def get_trajectories(
        self,
        session_id: str,
        fields: Optional[str] = None
    ) -> Dict[str, Any]:
        """查询指定 session 的所有轨迹记录

        Args:
            session_id: 会话ID
            fields: 逗号分隔的字段名，None 返回全部

        Returns:
            包含 session_id 和记录列表的字典
        """
        records = await self.request_repository.get_all_by_session(session_id, fields=fields)
        return {
            "session_id": session_id,
            "records": records
        }

    async def list_records(
        self,
        session_id: str,
        limit: int = 10000,
        fields: Optional[str] = None
    ) -> Dict[str, Any]:
        """查询指定 session 下的 record 元数据列表（含归档记录）"""
        records = await self.request_repository.get_metadata_by_session(
            session_id, limit=limit, fields=fields
        )
        return {
            "session_id": session_id,
            "records": records
        }

    async def get_record(
        self,
        session_id: str,
        request_id: str,
        fields: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """查询单条 record 详情（归档记录的详情字段为 None）"""
        return await self.request_repository.get_record_detail(
            session_id, request_id, fields=fields
        )

    async def get_route_experts(
        self,
        session_id: str,
        request_id: str,
    ) -> RouteExpertsFetchResult:
        """回拉 route_experts blob（fallback 端点业务逻辑）。

        逻辑（设计文档 九.3）：
            1. 依赖未注入（feature off）→ NotFound（404）
            2. ref = await ref_repo.get_for_fetch(session_id, request_id)
            3. ref is None → NotFound（404，含跨租户请求）
            4. status='uploading' → Uploading（202 Retry-After: 30）
            5. status='ready' → open_stream（await，错误同步抛出）→ Ready（200 流式返回）
            6. status='consumed' → Consumed（410，本期默认不触发）

        Args:
            session_id: 会话 ID，多租户授权键，参与 ref 复合查询（P0-#3 修复）。
            request_id: 请求 ID（r3_blob_refs 主键）。

        Returns:
            路由层穷尽匹配后映射为 HTTP 404/202/200/410 响应。

        Raises:
            DatabaseError: ref 查询失败时抛出，由路由层映射为 5xx。
            BlobStorageError: open_stream 失败时抛出，由路由层映射为 502
                （流式错误前置，可被路由 try/except 捕获）。
        """
        if self._r3_ref_repository is None or self._blob_storage is None:
            logger.info(
                f"[{session_id}/{request_id}] route_experts fallback 未启用"
                f"（R3RefRepository/BlobStorage 未注入），返回 404"
            )
            return RouteExpertsNotFound()

        ref = await self._r3_ref_repository.get_for_fetch(session_id, request_id)
        if ref is None:
            logger.info(
                f"[{session_id}/{request_id}] r3_blob_refs 无引用行或跨租户，返回 404"
            )
            return RouteExpertsNotFound()

        status = ref.get("status")
        blob_key = ref.get("blob_key")
        log_prefix = f"[{session_id}/{request_id}]"

        match status:
            case "uploading":
                logger.info(f"{log_prefix} route_experts 上传中，返回 202")
                return RouteExpertsUploading(retry_after_seconds=30)
            case "ready":
                if not blob_key:
                    logger.error(
                        f"{log_prefix} r3_blob_refs status=ready 但 blob_key 为空"
                    )
                    return RouteExpertsNotFound()
                logger.info(
                    f"{log_prefix} route_experts 就绪，打开流: key={blob_key}"
                )
                # open_stream 同步 await：认证/打开在此完成，失败抛 BlobStorageError
                # 由路由 try/except 捕获映射为 502（P0 修复：流式错误前置，不再延迟到 iter_bytes）
                # P0-2 修复：open_stream 成功后到 return 前若异常，必须 close handle
                # 防止 StreamHandle 资源泄漏（文件句柄/HTTP stream）
                handle = await self._blob_storage.open_stream(blob_key)
                try:
                    return RouteExpertsReady(
                        blob_key=blob_key,
                        stream_handle=handle,
                    )
                except Exception:
                    # 极端：dataclass 构造或 return 路径异常，handle 已打开必须释放
                    await handle.close()
                    raise
            case "consumed":
                logger.info(f"{log_prefix} route_experts 已消费，返回 410")
                return RouteExpertsConsumed()
            case _:
                logger.error(
                    f"{log_prefix} r3_blob_refs 未知 status={status!r}，返回 404"
                )
                return RouteExpertsNotFound()
