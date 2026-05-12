"""
FastAPI 路由定义

统一处理所有 HTTP 接口：
- 聊天补全接口
- 模型管理接口
- 轨迹查询接口
"""

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from typing import Dict, Any, Optional
import asyncio
import json
import traceback
import uuid

try:
    import orjson
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False

from traj_proxy.utils.logger import get_logger
from traj_proxy.serve.schemas import (
    RegisterModelRequest,
    RegisterModelResponse,
    DeleteModelResponse,
)
from traj_proxy.serve.dependencies import get_processor_manager
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.validators import (
    normalize_run_id,
    validate_model_for_inference,
    validate_session_id,
)
from traj_proxy.serve.error_handler import build_error_response

# 路由定义
chat_router = APIRouter()
model_router = APIRouter()
transcript_router = APIRouter()
trajectory_router = APIRouter()

logger = get_logger(__name__)


def _serialize_json(obj: Any) -> bytes:
    """序列化对象为 JSON bytes

    优先使用 orjson（2-5x 更快），不可用时回退到 stdlib json。
    返回 bytes 供 Response(content=...) 直接使用。
    """
    if _HAS_ORJSON:
        return orjson.dumps(obj)
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _extract_run_id(model: str, x_run_id: Optional[str], run_id: Optional[str]) -> Optional[str]:
    """
    从多个来源提取 run_id

    优先级：
    1. 路径参数（run_id）
    2. x-run-id Header
    3. model 参数逗号后（model_name,run_id）

    Args:
        model: 模型参数，支持格式：model_name 或 model_name,run_id
        x_run_id: x-run-id header 值
        run_id: FastAPI 路径参数中的 run_id

    Returns:
        run_id 或 None
    """
    # 优先级1：路径参数（最高）
    if run_id:
        return normalize_run_id(run_id.strip())

    # 优先级2：x-run-id header
    if x_run_id:
        return normalize_run_id(x_run_id.strip())

    # 优先级3：model 参数逗号后
    if ',' in model:
        return normalize_run_id(model.split(',', 1)[1].strip())

    return None


def _extract_actual_model(model: str) -> str:
    """
    从 model 参数中提取实际的 model_name

    Args:
        model: 模型参数，支持格式：model_name 或 model_name,run_id

    Returns:
        实际的 model_name
    """
    if ',' in model:
        return model.split(',', 1)[0].strip()
    return model.strip()


# 不应转发到推理服务的 header（HTTP 基础 + TrajProxy 内部）
HEADER_BLACKLIST = {
    # HTTP 基础 header（由 requests 库自动处理）
    "host", "content-length", "content-type", "connection",
    "accept", "accept-encoding", "user-agent",
    # TrajProxy 内部使用
    "authorization",      # 由 InferClient 独立管理
    "x-run-id",           # 已用于模型路由
    "x-session-id",       # 已用于会话存储
}


def _extract_forward_headers(request: Request) -> Dict[str, str]:
    """
    提取需要转发到推理服务的 header（黑名单模式）

    转发所有不在黑名单中的 header，使推理服务可获取追踪信息等。

    Args:
        request: FastAPI 请求对象

    Returns:
        需要转发的 header 字典
    """
    forward_headers = {}
    for key, value in request.headers.items():
        if key.lower() not in HEADER_BLACKLIST:
            forward_headers[key] = value
    return forward_headers


# ========== 聊天补全接口 ==========

@chat_router.post("/chat/completions")
async def chat_completions(
    request: Request,
    background_tasks: BackgroundTasks,
    run_id: Optional[str] = None,
    session_id: Optional[str] = None
):
    """
    处理聊天补全请求（支持流式和非流式）

    参数:
        request: FastAPI请求对象
        background_tasks: 后台任务
        run_id: 路径参数中的运行ID（可选）
        session_id: 路径参数中的会话ID（可选）

    返回:
        处理后的响应（JSON 或 SSE 流）
    """
    request_id = str(uuid.uuid4())

    # 并发限流：获取信号量，超时 5s 返回 429
    semaphore = getattr(request.app.state, "request_semaphore", None)
    acquired = False
    if semaphore:
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=5.0)
            acquired = True
        except asyncio.TimeoutError:
            max_conc = getattr(request.app.state, "max_concurrent_requests", "?")
            logger.warning(
                f"[{request_id}] 并发限流拒绝: max_concurrent={max_conc} 已达上限"
            )
            raise HTTPException(
                status_code=429,
                detail="服务繁忙，请稍后重试",
                headers={"Retry-After": "3"}
            )

    try:
        # 获取请求体
        body = await request.json()
        # 仅记录关键元信息，避免日志冲刷

        # 提取请求参数
        messages = body.get("messages", [])
        model = body.get("model")

        # 从 header 获取 run_id 和 session_id
        x_run_id = request.headers.get("x-run-id")
        x_session_id = request.headers.get("x-session-id")
        x_sandbox_traj_id = request.headers.get("x-sandbox-traj-id")

        # session_id 优先级：路径参数 > x-session-id > x-sandbox-traj-id
        final_session_id = session_id or x_session_id or x_sandbox_traj_id

        # 如果 session_id 为空字符串，设为 None
        if final_session_id == "":
            final_session_id = None

        stream = body.get("stream", False)  # 检查 stream 参数

        # 校验 model 参数格式
        valid, msg, _ = validate_model_for_inference(model or "")
        if not valid:
            raise HTTPException(status_code=422, detail=msg)

        # 提取 run_id（优先级：路径参数 > x-run-id header > model 参数）
        final_run_id = _extract_run_id(model, x_run_id, run_id)

        # 提取实际 model_name
        actual_model = _extract_actual_model(model)

        # 提取需要转发到推理服务的 header（黑名单模式）
        forward_headers = _extract_forward_headers(request)

        # 其他请求参数（黑名单模式：排除 model, messages, stream）
        PARAM_BLACKLIST = {"model", "messages", "stream"}
        request_params = {k: v for k, v in body.items() if k not in PARAM_BLACKLIST}

        # 生成 request_id（已在 try 外部生成）

        logger.info(f"[{request_id}] 处理聊天补全请求: model={actual_model}, run_id={final_run_id}, session_id={final_session_id}, stream={stream}, messages={len(messages)}")

        # 获取 ProcessorManager 实例（从请求上下文）
        processor_manager = get_processor_manager(request)

        # 根据 run_id 和 model_name 获取对应的 processor（懒加载）
        processor = await processor_manager.get_processor_async(final_run_id, actual_model)

        if processor is None:
            # 本地未找到模型，尝试从数据库查询（回退机制）
            # 用于处理 LISTEN/NOTIFY 通知延迟导致的竞态条件
            logger.info(f"[{request_id}] 本地未找到模型，尝试 DB 回退查询: model={actual_model}, run_id={final_run_id}")
            processor = await processor_manager.try_get_or_sync_from_db(final_run_id, actual_model)

        if processor is None:
            logger.warning(f"[{request_id}] 模型未注册: model={actual_model}, run_id={final_run_id}")
            raise HTTPException(
                status_code=404,
                detail=f"模型 '{actual_model}' 未注册 (run_id={final_run_id})"
            )

        # 根据是否流式选择处理方式
        if stream:
            # 流式处理 - 使用 Processor.process_stream
            # 上下文容器，流式完成后用于后台存储
            context_holder = {}

            async def generate_stream():
                """流式生成器，捕获异常并发送错误 SSE 事件"""
                try:
                    async for chunk in processor.process_stream(
                        messages=messages,
                        request_id=request_id,
                        session_id=final_session_id,
                        run_id=final_run_id,
                        context_holder=context_holder,
                        forward_headers=forward_headers,
                        **request_params
                    ):
                        # chunk 可能是 dict（OpenAI 格式），需要序列化为 SSE 格式
                        if isinstance(chunk, dict):
                            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                        else:
                            yield chunk
                    # SSE 流结束标记
                    yield "data: [DONE]\n\n"
                except Exception as stream_err:
                    logger.exception(f"[{request_id}] 流式处理异常: {str(stream_err)}")
                    error_body, _ = build_error_response(request_id, stream_err)
                    yield f"data: {json.dumps({'error': error_body}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"

            # 注意：存储已在 processor._finalize_stream() 中完成，无需后台任务

            return StreamingResponse(
                generate_stream(),
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
                session_id=final_session_id,
                run_id=final_run_id,
                forward_headers=forward_headers,
                **request_params
            )

            # 返回 OpenAI 格式响应
            return context.raw_response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[{request_id}] 聊天补全请求处理失败: {str(e)}")
        error_detail, status_code = build_error_response(request_id, e)
        raise HTTPException(status_code=status_code, detail=error_detail)
    finally:
        if acquired:
            semaphore.release()


# ========== 模型管理接口 ==========

@model_router.post("/register", response_model=RegisterModelResponse)
async def register_model(request: Request, req: RegisterModelRequest):
    """
    注册新模型（模型会自动同步到所有 Worker）

    参数:
        request: FastAPI Request 对象
        req: 注册模型请求

    返回:
        注册结果
    """
    try:
        processor_manager = get_processor_manager(request)

        # run_id 可以为空，替换标准化处理
        run_id = normalize_run_id(req.run_id.strip())

        # 注册模型（会同步持久化到数据库）
        config = await processor_manager.register_dynamic_processor(
            model_name=req.model_name,
            url=req.url,
            api_key=req.api_key,
            tokenizer_path=req.tokenizer_path,
            token_in_token_out=req.token_in_token_out,
            persist_to_db=True,
            run_id=run_id,
            tool_parser=req.tool_parser,
            reasoning_parser=req.reasoning_parser
        )

        logger.info(f"[{req.model_name}] 注册模型成功: run_id={run_id}")

        return RegisterModelResponse(
            status="success",
            run_id=normalize_run_id(run_id),
            model_name=req.model_name,
            detail={
                "run_id": config.run_id,
                "model": config.model_name,
                "tokenizer_path": config.tokenizer_path,
                "token_in_token_out": config.token_in_token_out,
                "tool_parser": config.tool_parser,
                "reasoning_parser": config.reasoning_parser,
                "sync_info": "模型已持久化到数据库，其他 Worker 已通过 LISTEN/NOTIFY 即时通知"
            }
        )

    except ValueError as e:
        # 模型已存在
        logger.warning(f"注册模型失败: {str(e)}")
        raise HTTPException(status_code=400, detail={
            "type": "model_already_exists",
            "message": str(e),
        })
    except DatabaseError as e:
        logger.error(f"数据库错误: {str(e)}")
        error_detail, status_code = build_error_response("register_model", e)
        raise HTTPException(status_code=status_code, detail=error_detail)
    except Exception as e:
        logger.error(f"注册模型异常: {str(e)}\n{traceback.format_exc()}")
        error_detail, status_code = build_error_response("register_model", e)
        raise HTTPException(status_code=status_code, detail=error_detail)


@model_router.delete("", response_model=DeleteModelResponse)
async def delete_model(request: Request, model_name: str, run_id: str = ""):
    """
    删除已注册的模型（会自动从所有 Worker 中删除）

    参数:
        request: FastAPI Request 对象
        model_name: 模型名称
        run_id: 运行ID（查询参数，默认为空字符串表示使用 DEFAULT）

    返回:
        删除结果
    """
    try:
        processor_manager = get_processor_manager(request)

        # run_id 可以为空，替换标准化处理
        actual_run_id = normalize_run_id(run_id.strip())

        deleted = await processor_manager.unregister_dynamic_processor(
            model_name, persist_to_db=True, run_id=actual_run_id
        )

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"模型 '{model_name}' 不存在 (run_id={actual_run_id})"
            )

        logger.info(f"[{model_name}] 删除模型成功: run_id={actual_run_id}")

        return DeleteModelResponse(
            status="success",
            run_id=actual_run_id,
            model_name=model_name,
            deleted=True
        )

    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error(f"数据库错误: {str(e)}")
        error_detail, status_code = build_error_response("delete_model", e)
        raise HTTPException(status_code=status_code, detail=error_detail)
    except Exception as e:
        logger.error(f"删除模型异常: {str(e)}\n{traceback.format_exc()}")
        error_detail, status_code = build_error_response("delete_model", e)
        raise HTTPException(status_code=status_code, detail=error_detail)


@model_router.get("")
async def list_models(request: Request):
    """
    列出所有已注册模型（管理格式，包含详细信息）

    返回:
        所有模型的详细信息列表
    """
    try:
        processor_manager = get_processor_manager(request)
        models_info = processor_manager.get_all_processors_info()

        return {
            "status": "success",
            "count": len(models_info),
            "models": models_info
        }

    except Exception as e:
        logger.error(f"列出模型异常: {str(e)}\n{traceback.format_exc()}")
        error_detail, status_code = build_error_response("list_models", e)
        raise HTTPException(status_code=status_code, detail=error_detail)


# ========== OpenAI 兼容接口 ==========

@chat_router.get("/models")
async def list_models_openai(request: Request):
    """
    列出所有已注册模型（OpenAI 兼容格式）

    返回:
        OpenAI 格式的模型列表: {"object": "list", "data": [...]}
    """
    try:
        processor_manager = get_processor_manager(request)
        models_info = processor_manager.get_all_processors_info()

        # 转换为 OpenAI 格式
        model_list = []
        for info in models_info:
            if info:
                model_id = f"{info['model_name']},{info['run_id']}" if info.get('run_id') else info['model_name']
                model_list.append({
                    "id": model_id,
                    "object": "model",
                    "created": 1677610602,
                    "owned_by": "organization-owner"
                })

        return {
            "object": "list",
            "data": model_list
        }

    except Exception as e:
        logger.error(f"列出模型异常: {str(e)}\n{traceback.format_exc()}")
        error_detail, status_code = build_error_response("list_models_openai", e)
        raise HTTPException(status_code=status_code, detail=error_detail)


# ========== 轨迹查询接口 ==========

@transcript_router.get("/trajectory")
async def get_trajectory(
    request: Request,
    session_id: str,
    limit: int = 10000
) -> Dict[str, Any]:
    """
    根据 session_id 获取所有轨迹记录（旧接口，保持向后兼容）

    参数:
        request: FastAPI Request 对象
        session_id: 会话ID (格式: app_id,sample_id,task_id)
        limit: 最多返回的记录数，默认为10000

    返回:
        包含session_id、记录数量和记录列表的字典

    Raises:
        HTTPException: 当查询失败时抛出
    """
    from traj_proxy.workers.worker import get_transcript_provider as get_provider

    # 校验 session_id
    valid, msg = validate_session_id(session_id)
    if not valid:
        raise HTTPException(status_code=422, detail=msg)

    try:
        provider = get_provider(request)
        # 使用新方法查询，然后转换为旧格式
        result = await provider.get_trajectories(session_id)
        records = result["records"][:limit]
        result_data = {
            "session_id": session_id,
            "count": len(records),
            "records": records
        }
        # 在线程池中序列化大 JSON，避免阻塞事件循环导致连接重置
        json_bytes = await asyncio.to_thread(_serialize_json, result_data)
        return Response(content=json_bytes, media_type="application/json")
    except Exception as e:
        logger.exception(f"轨迹查询失败: {str(e)}")
        error_detail, status_code = build_error_response("trajectory_query", e)
        raise HTTPException(status_code=status_code, detail=error_detail)


# ========== 轨迹查询接口（新版） ==========

@trajectory_router.get("")
async def list_trajectories(
    request: Request,
    run_id: str
) -> Dict[str, Any]:
    """
    查询轨迹列表（可按 run_id 过滤）

    参数:
        request: FastAPI Request 对象
        run_id: 运行ID（必填）

    返回:
        包含 run_id 和轨迹列表的字典

    Raises:
        HTTPException: 当查询失败时抛出
    """
    from traj_proxy.workers.worker import get_transcript_provider as get_provider

    try:
        provider = get_provider(request)
        return await provider.list_trajectories(run_id)
    except Exception as e:
        logger.exception(f"查询轨迹列表失败: {str(e)}")
        error_detail, status_code = build_error_response("list_trajectories", e)
        raise HTTPException(status_code=status_code, detail=error_detail)


@trajectory_router.get("/{session_id}")
async def get_trajectory_detail(
    request: Request,
    session_id: str,
    limit: int = 10000
) -> Dict[str, Any]:
    """
    查询指定轨迹的完整数据（详情）

    参数:
        request: FastAPI Request 对象
        session_id: 会话ID
        limit: 最多返回的记录数，默认为10000

    返回:
        包含 session_id 和记录列表的字典

    Raises:
        HTTPException: 当查询失败时抛出
    """
    from traj_proxy.workers.worker import get_transcript_provider as get_provider

    try:
        provider = get_provider(request)
        result = await provider.get_trajectories(session_id)
        # 应用 limit 限制
        if limit and len(result["records"]) > limit:
            result["records"] = result["records"][:limit]
        # 在线程池中序列化大 JSON，避免阻塞事件循环导致连接重置
        json_bytes = await asyncio.to_thread(_serialize_json, result)
        return Response(content=json_bytes, media_type="application/json")
    except Exception as e:
        logger.exception(f"查询轨迹详情失败: {str(e)}")
        error_detail, status_code = build_error_response("get_trajectory_detail", e)
        raise HTTPException(status_code=status_code, detail=error_detail)
