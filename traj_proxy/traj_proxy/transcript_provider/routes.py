"""
TranscriptProvider FastAPI路由

处理轨迹记录查询相关路由
"""

from fastapi import APIRouter, Request, HTTPException, Query
from typing import Dict, Any, Optional
from traj_proxy.store.database import DatabaseManager

from traj_proxy.transcript_provider.worker import get_db_manager

router = APIRouter()


@router.get("/health")
async def health():
    """
    健康检查接口

    返回:
        健康状态信息
    """
    return {"status": "ok"}


@router.get("/session/{session_id}")
async def get_session_records(
    session_id: str,
    limit: int = 100
) -> Dict[str, Any]:
    """
    根据 session_id 获取所有匹配的 request_records

    参数:
        session_id: 会话ID (格式: app_id#sample_id#task_id)
        limit: 最多返回的记录数，默认为100

    返回:
        包含session_id、记录数量和记录列表的字典

    Raises:
        HTTPException: 当查询失败时抛出
    """
    try:
        db_manager = get_db_manager()
        records = await db_manager.get_request_records_by_session(session_id, limit)
        return {
            "session_id": session_id,
            "count": len(records),
            "records": records
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process")
async def process_transcript(request: Request):
    """
    查询单条轨迹记录

    根据 request_id 或 unique_id 查询请求记录详情。

    参数:
        request: FastAPI请求对象，包含 request_id 或 unique_id

    返回:
        轨迹记录详情
    """
    try:
        # 获取请求体
        data = await request.json()
        request_id = data.get("request_id")
        unique_id = data.get("unique_id")

        db_manager = get_db_manager()

        # 根据 unique_id 优先查询
        if unique_id:
            record = await db_manager.get_trajectory_by_unique_id(unique_id)
        elif request_id:
            record = await db_manager.get_trajectory_by_request_id(request_id)
        else:
            raise HTTPException(status_code=400, detail="必须提供 request_id 或 unique_id")

        if record is None:
            raise HTTPException(status_code=404, detail="未找到对应的轨迹记录")

        return {
            "status": "found",
            "record": record
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transcribe")
async def transcribe_audio(request: Request):
    """
    查询轨迹记录的响应文本

    根据 request_id 或 unique_id 查询请求的响应文本。

    参数:
        request: FastAPI请求对象，包含 request_id 或 unique_id

    返回:
        响应文本及相关信息
    """
    try:
        # 获取请求体
        data = await request.json()
        request_id = data.get("request_id")
        unique_id = data.get("unique_id")

        db_manager = get_db_manager()

        # 根据 unique_id 优先查询
        if unique_id:
            record = await db_manager.get_trajectory_by_unique_id(unique_id)
        elif request_id:
            record = await db_manager.get_trajectory_by_request_id(request_id)
        else:
            raise HTTPException(status_code=400, detail="必须提供 request_id 或 unique_id")

        if record is None:
            raise HTTPException(status_code=404, detail="未找到对应的轨迹记录")

        return {
            "status": "found",
            "request_id": record.get("request_id"),
            "unique_id": record.get("unique_id"),
            "response_text": record.get("response_text"),
            "response": record.get("response"),
            "model": record.get("model"),
            "prompt_tokens": record.get("prompt_tokens"),
            "completion_tokens": record.get("completion_tokens"),
            "total_tokens": record.get("total_tokens")
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/segment")
async def segment_transcript(request: Request):
    """
    查询轨迹记录的分段信息

    根据 session_id 查询该会话的所有轨迹记录。

    参数:
        request: FastAPI请求对象，包含 session_id 和可选的 limit

    返回:
        轨迹记录分段列表
    """
    try:
        # 获取请求体
        data = await request.json()
        session_id = data.get("session_id")
        limit = data.get("limit", 100)

        if not session_id:
            raise HTTPException(status_code=400, detail="必须提供 session_id")

        db_manager = get_db_manager()

        # 查询该 session 的所有记录
        records = await db_manager.get_request_records_by_session(session_id, limit)

        # 构建分段信息
        segments = [
            {
                "id": idx + 1,
                "request_id": record.get("request_id"),
                "unique_id": record.get("unique_id"),
                "prompt_text": record.get("prompt_text"),
                "response_text": record.get("response_text"),
                "prompt_tokens": record.get("prompt_tokens"),
                "completion_tokens": record.get("completion_tokens"),
                "start_time": record.get("start_time").isoformat() if record.get("start_time") else None
            }
            for idx, record in enumerate(records)
        ]

        return {
            "status": "found",
            "session_id": session_id,
            "count": len(records),
            "segments": segments
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
