"""
ProxyCore FastAPI路由

处理LLM请求转发相关路由
"""

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from traj_proxy.utils.logger import get_logger
from traj_proxy.workers.worker import get_processor_manager
from traj_proxy.proxy_core.processor_manager import (
    RegisterModelRequest,
    RegisterModelResponse,
    DeleteModelResponse,
    ListModelsResponse,
    ModelInfo
)
from traj_proxy.proxy_core.streaming import StreamingResponseGenerator
from traj_proxy.exceptions import DatabaseError
import uuid

router = APIRouter()
admin_router = APIRouter()
logger = get_logger(__name__)


@router.post("/chat/completions")
async def chat_completions(request: Request, background_tasks: BackgroundTasks):
    """
    处理聊天补全请求（支持流式和非流式）

    参数:
        request: FastAPI请求对象
        background_tasks: 后台任务

    返回:
        处理后的响应（JSON 或 SSE 流）
    """
    try:
        # 获取请求体
        body = await request.json()

        # 提取请求参数
        messages = body.get("messages", [])
        model = body.get("model")
        session_id = request.headers.get("x-session-id")
        stream = body.get("stream", False)  # 检查 stream 参数

        # 如果 header 中没有，尝试从 request.state 获取（由中间件从路径中提取）
        if not session_id and hasattr(request.state, "session_id_from_path"):
            session_id = request.state.session_id_from_path

        # 如果 session_id 为空，但 model 包含 @，解析 model 获取 session_id
        if not session_id and model and "@" in model:
            parts = model.split("@", 1)
            model = parts[0]
            session_id = parts[1]

        # session_id 是必须的，用于解析 job_id 路由到正确的 processor
        if not session_id:
            raise HTTPException(
                status_code=400,
                detail="缺少 session_id，无法路由请求。请在 header 中提供 x-session-id，格式为 {job_id}#{sample_id}#{task_id}"
            )

        # 其他请求参数
        request_params = {}
        for key in ["max_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty"]:
            if key in body:
                request_params[key] = body[key]

        # 生成 request_id
        request_id = str(uuid.uuid4())

        logger.info(f"处理聊天补全请求: model={model}, stream={stream}, messages={len(messages)}, session_id={session_id}")

        # 获取 ProcessorManager 实例
        processor_manager = get_processor_manager()

        # 根据 session_id 解析 job_id，结合 model_name 获取对应的 processor
        processor = processor_manager.get_processor_by_session(model, session_id)
        if processor is None:
            logger.warning(f"模型未注册: {model}, session_id={session_id}")
            raise HTTPException(
                status_code=404,
                detail=f"模型 '{model}' 未注册"
            )

        # 根据是否流式选择处理方式
        if stream:
            # 流式处理
            generator = StreamingResponseGenerator(
                processor=processor,
                messages=messages,
                request_id=request_id,
                session_id=session_id,
                **request_params
            )

            # 注册后台任务：流式结束后存储到数据库
            async def save_stream_record():
                ctx = processor.get_stream_context()
                if ctx and processor.request_repository:
                    try:
                        await processor.request_repository.insert(ctx, processor.tokenizer_path)
                        logger.info(f"[{ctx.unique_id}] 流式记录存储成功")
                    except Exception as e:
                        import traceback
                        logger.error(f"[{ctx.unique_id}] 流式记录存储失败: {e}\n{traceback.format_exc()}")

            background_tasks.add_task(save_stream_record)

            return StreamingResponse(
                generator.generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
                }
            )
        else:
            # 非流式处理
            context = await processor.process_request(
                messages=messages,
                request_id=request_id,
                session_id=session_id,
                **request_params
            )

            # 返回 OpenAI 格式响应
            return context.response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"聊天补全请求处理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def list_models():
    """
    列出可用模型

    返回:
        可用模型列表
    """
    # 获取 ProcessorManager 实例
    processor_manager = get_processor_manager()
    model_keys = processor_manager.list_models()

    # 构建 OpenAI 格式的响应，id 格式为 job_id/model_name
    data = [
        ModelInfo(id=f"{job_id}/{model_name}" if job_id else model_name)
        for job_id, model_name in model_keys
    ]

    return ListModelsResponse(data=data)


# ========== 管理接口 ==========

@admin_router.post("/register", response_model=RegisterModelResponse)
async def register_model(request: RegisterModelRequest):
    """
    注册新模型（模型会自动同步到所有 Worker）

    参数:
        request: 注册模型请求

    返回:
        注册结果
    """
    try:
        processor_manager = get_processor_manager()

        # 注册模型（会同步持久化到数据库）
        processor = await processor_manager.register_dynamic_processor(
            model_name=request.model_name,
            url=request.url,
            api_key=request.api_key,
            tokenizer_path=request.tokenizer_path,
            token_in_token_out=request.token_in_token_out,
            persist_to_db=True,
            job_id=request.job_id,
            tool_parser=request.tool_parser,
            reasoning_parser=request.reasoning_parser
        )

        logger.info(f"注册模型成功: job_id={request.job_id}, model_name={request.model_name}")

        return RegisterModelResponse(
            status="success",
            job_id=request.job_id,
            model_name=request.model_name,
            detail={
                "job_id": processor.job_id,
                "model": processor.model,
                "tokenizer_path": processor.tokenizer_path,
                "token_in_token_out": processor.token_in_token_out,
                "tool_parser": processor.tool_parser_name,
                "reasoning_parser": processor.reasoning_parser_name,
                "sync_info": "模型已持久化到数据库，其他 Worker 将在 30 秒内自动同步"
            }
        )

    except ValueError as e:
        # 模型已存在
        logger.warning(f"注册模型失败: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except DatabaseError as e:
        logger.error(f"数据库错误: {str(e)}")
        raise HTTPException(status_code=503, detail=f"数据库不可用: {str(e)}")
    except Exception as e:
        import traceback
        logger.error(f"注册模型异常: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"注册模型失败: {str(e)}")


@admin_router.delete("/{model_name}", response_model=DeleteModelResponse)
async def delete_model(model_name: str, job_id: str = ""):
    """
    删除已注册的模型（会自动从所有 Worker 中删除）

    参数:
        model_name: 模型名称
        job_id: 作业ID（查询参数，默认为空字符串表示全局模型）

    返回:
        删除结果
    """
    try:
        processor_manager = get_processor_manager()

        deleted = await processor_manager.unregister_dynamic_processor(model_name, persist_to_db=True, job_id=job_id)

        if not deleted:
            raise HTTPException(status_code=404, detail=f"模型 '{model_name}' 不存在 (job_id={job_id})")

        logger.info(f"删除模型成功: job_id={job_id}, model_name={model_name}")

        return DeleteModelResponse(
            status="success",
            job_id=job_id,
            model_name=model_name,
            deleted=True
        )

    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error(f"数据库错误: {str(e)}")
        raise HTTPException(status_code=503, detail=f"数据库不可用: {str(e)}")
    except Exception as e:
        import traceback
        logger.error(f"删除模型异常: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"删除模型失败: {str(e)}")


@admin_router.get("")
async def list_admin_models():
    """
    列出所有已注册模型（包含详细信息）

    返回:
        所有模型的详细信息列表
    """
    try:
        processor_manager = get_processor_manager()
        models_info = processor_manager.get_all_processors_info()

        return {
            "status": "success",
            "count": len(models_info),
            "models": models_info
        }

    except Exception as e:
        import traceback
        logger.error(f"列出模型异常: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"列出模型失败: {str(e)}")
