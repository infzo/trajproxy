"""
TranscriptProvider FastAPI路由

处理轨迹记录查询相关路由
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from traj_proxy.workers.worker import get_transcript_provider as get_provider

router = APIRouter()


@router.get("/health")
async def health():
    """
    健康检查接口

    返回:
        健康状态信息
    """
    return {"status": "ok"}


@router.get("/trajectory")
async def get_trajectory(
    session_id: str,
    limit: int = 10000
) -> Dict[str, Any]:
    """
    根据 session_id 获取所有轨迹记录

    参数:
        session_id: 会话ID (格式: app_id;sample_id;task_id)
        limit: 最多返回的记录数，默认为100

    返回:
        包含session_id、记录数量和记录列表的字典

    Raises:
        HTTPException: 当查询失败时抛出
    """
    try:
        provider = get_provider()
        return await provider.get_trajectory(session_id, limit)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{str(e)}\n{traceback.format_exc()}")


