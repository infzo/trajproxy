"""
ProxyCore FastAPI路由

处理LLM请求转发相关路由
"""

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from typing import Dict, Any, Optional
from traj_proxy.utils.logger import get_logger
from traj_proxy.proxy_core.worker import get_processor
import uuid

router = APIRouter()
logger = get_logger(__name__)


@router.get("/health")
async def health():
    """
    健康检查接口

    返回:
        健康状态信息
    """
    return {"status": "ok"}


@router.post("/chat/completions")
async def chat_completions(request: Request, background_tasks: BackgroundTasks):
    """
    处理聊天补全请求

    参数:
        request: FastAPI请求对象
        background_tasks: 后台任务

    返回:
        处理后的响应
    """
    try:
        # 获取请求体
        body = await request.json()

        # 提取请求参数
        messages = body.get("messages", [])
        model = body.get("model")
        session_id = body.get("session_id")

        # 其他请求参数
        request_params = {}
        for key in ["max_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty"]:
            if key in body:
                request_params[key] = body[key]

        # 生成 request_id
        request_id = str(uuid.uuid4())

        logger.info(f"处理聊天补全请求: model={model}, messages={len(messages)}, session_id={session_id}")

        # 获取 Processor 实例
        processor = get_processor()

        # 更新 model（如果请求中指定了不同的 model）
        original_model = processor.model
        if model and model != original_model:
            processor.model = model

        # 处理请求
        context = await processor.process_request(
            messages=messages,
            request_id=request_id,
            session_id=session_id,
            **request_params
        )

        # 恢复原始 model
        processor.model = original_model

        # 返回 OpenAI 格式响应
        return context.response

    except Exception as e:
        logger.error(f"聊天补全请求处理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def list_models():
    """
    列出可用模型

    返回:
        可用模型列表
    """
    # 获取 Processor 实例中的模型配置
    processor = get_processor()
    model_name = processor.model

    return {
        "object": "list",
        "data": [
            {
                "id": model_name,
                "object": "model",
                "created": 1677610602,
                "owned_by": "organization-owner"
            }
        ]
    }
