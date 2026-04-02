"""
ProxyCore FastAPI路由

处理LLM请求转发相关路由
"""

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from traj_proxy.utils.logger import get_logger
from traj_proxy.proxy_core.processor_manager import (
    RegisterModelRequest,
    RegisterModelResponse,
    DeleteModelResponse,
)
from traj_proxy.proxy_core.streaming import StreamingResponseGenerator
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.validators import (
    validate_session_id,
    validate_model_name,
    validate_model_for_inference,
    normalize_run_id,
    DEFAULT_RUN_ID,
)
import traceback
import uuid

chat_router = APIRouter()
model_router = APIRouter()
logger = get_logger(__name__)


def _get_processor_manager(request: Request):
    """
    从请求上下文获取 ProcessorManager

    Args:
        request: FastAPI Request 对象

    Returns:
        ProcessorManager 实例

    Raises:
        HTTPException: 如果 ProcessorManager 未初始化
    """
    pm = getattr(request.app.state, "processor_manager", None)
    if pm is None:
        logger.error("ProcessorManager 未初始化")
        raise HTTPException(status_code=500, detail="服务未初始化")
    return pm


def _parse_model_and_run_id(model: str, session_id: str = None) -> tuple:
    """
    解析 model 和 session_id，返回 (实际model, run_id)

    优先级：
    1. model 中包含逗号 -> 从 model 解析 run_id（格式：model_name,run_id）
    2. session_id 中包含逗号 -> 从 session_id 解析 run_id（格式：run_id,sample_id,task_id）
    3. 默认 -> run_id = DEFAULT

    Args:
        model: 模型参数，支持两种格式：
               - model_name (无逗号)
               - model_name,run_id (有逗号)
        session_id: 会话ID，格式为 run_id,sample_id,task_id

    Returns:
        (实际model_name, run_id)
    """
    actual_model = model

    # 场景二：model 中有逗号，格式为 {model_name},{run_id}
    if ',' in model:
        parts = model.split(',', 1)
        actual_model = parts[0].strip()
        run_id = parts[1].strip()
        return actual_model, run_id

    # 场景一：model 中无逗号
    # 1.3: session_id 有逗号，提取 run_id
    if session_id and ',' in session_id:
        run_id = session_id.split(',')[0].strip()
        return actual_model, run_id

    # 1.1, 1.2: 默认 run_id
    return actual_model, normalize_run_id(None)


@chat_router.post("/chat/completions")
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
        print(f"####### {body}")

        # 提取请求参数
        messages = body.get("messages", [])
        model = body.get("model")
        x_session_id = request.headers.get("x-session-id")
        x_sandbox_traj_id = request.headers.get("x-sandbox-traj-id")
        session_id = x_sandbox_traj_id or x_session_id  # 优先使用 x-sandbox-traj-id
        stream = body.get("stream", False)  # 检查 stream 参数

        # 如果 header 中没有，尝试从 request.state 获取（由中间件从路径中提取）
        if not session_id and hasattr(request.state, "session_id_from_path"):
            session_id = request.state.session_id_from_path

        # 校验 model 参数格式
        valid, msg, _ = validate_model_for_inference(model or "")
        if not valid:
            raise HTTPException(status_code=422, detail=msg)

        # 解析 model 和 run_id（使用原始 model 参数）
        actual_model, run_id = _parse_model_and_run_id(model, session_id)

        # 其他请求参数
        request_params = {}
        for key in ["max_tokens", "max_completion_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty",
                    "tools", "tool_choice", "parallel_tool_calls", "documents", "stream_options"]:
            if key in body:
                request_params[key] = body[key]

        # 生成 request_id
        request_id = str(uuid.uuid4())

        logger.info(f"[{request_id}] 处理聊天补全请求: model={actual_model}, run_id={run_id}, stream={stream}, messages={len(messages)}")

        # 获取 ProcessorManager 实例（从请求上下文）
        processor_manager = _get_processor_manager(request)

        # 根据 run_id 和 model_name 获取对应的 processor
        processor = processor_manager.get_processor(run_id, actual_model)

        if processor is None:
            logger.warning(f"[{request_id}] 模型未注册: model={actual_model}, run_id={run_id}")
            raise HTTPException(
                status_code=404,
                detail=f"模型 '{actual_model}' 未注册 (run_id={run_id})"
            )

        # 根据是否流式选择处理方式
        if stream:
            # 流式处理 - 使用 StreamingProcessor
            streaming_processor = processor.streaming_processor
            generator = StreamingResponseGenerator(
                streaming_processor=streaming_processor,
                messages=messages,
                request_id=request_id,
                session_id=session_id,
                **request_params
            )

            # 注册后台任务：流式结束后存储到数据库
            async def save_stream_record():
                ctx = generator.get_completed_context()
                if ctx and streaming_processor.request_repository:
                    try:
                        await streaming_processor.request_repository.insert(ctx, streaming_processor.tokenizer_path)
                        logger.info(f"[{ctx.unique_id}] 流式记录存储成功")
                    except Exception as e:
                        logger.error(f"[{ctx.unique_id}] 流式记录存储失败: {e}", exc_info=True)

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
            return context.raw_response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"聊天补全请求处理失败: {str(e)}")
        # 生产环境不返回详细错误信息
        raise HTTPException(status_code=500, detail="内部服务错误，请查看日志获取详细信息")


# ========== 管理接口 ==========

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
        processor_manager = _get_processor_manager(request)

        # 场景一：run_id 为空，赋默认值 DEFAULT
        # 场景二：run_id 不为空，直接使用
        run_id = normalize_run_id(req.run_id)

        # 注册模型（会同步持久化到数据库）
        processor = await processor_manager.register_dynamic_processor(
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
            run_id=run_id,
            model_name=req.model_name,
            detail={
                "run_id": processor.run_id,
                "model": processor.model,
                "tokenizer_path": processor.tokenizer_path,
                "token_in_token_out": processor.token_in_token_out,
                "tool_parser": processor.tool_parser_name,
                "reasoning_parser": processor.reasoning_parser_name,
                "sync_info": "模型已持久化到数据库，其他 Worker 已通过 LISTEN/NOTIFY 即时通知"
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
        logger.error(f"注册模型异常: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"注册模型失败: {str(e)}")


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
        processor_manager = _get_processor_manager(request)

        # 场景一：run_id 为空，赋默认值 DEFAULT
        # 场景二：run_id 不为空，直接使用
        actual_run_id = normalize_run_id(run_id)

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
        raise HTTPException(status_code=503, detail=f"数据库不可用: {str(e)}")
    except Exception as e:
        logger.error(f"删除模型异常: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"删除模型失败: {str(e)}")


@model_router.get("")
async def list_models_admin(request: Request):
    """
    列出所有已注册模型（管理格式，包含详细信息）

    返回:
        所有模型的详细信息列表
    """
    try:
        processor_manager = _get_processor_manager(request)
        models_info = processor_manager.get_all_processors_info()

        return {
            "status": "success",
            "count": len(models_info),
            "models": models_info
        }

    except Exception as e:
        logger.error(f"列出模型异常: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"列出模型失败: {str(e)}")


# OpenAI 兼容的模型列表路由
@chat_router.get("/models")
async def list_models_openai(request: Request):
    """
    列出所有已注册模型（OpenAI 兼容格式）

    返回:
        OpenAI 格式的模型列表: {"object": "list", "data": [...]}
    """
    try:
        processor_manager = _get_processor_manager(request)
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
        raise HTTPException(status_code=500, detail=f"列出模型失败: {str(e)}")
