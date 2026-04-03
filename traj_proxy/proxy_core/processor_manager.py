"""
ProcessorManager - 多模型处理器管理器

管理多个 Processor 实例，支持动态注册、删除和查询。
"""

from typing import Dict, Optional, List, Tuple
from pydantic import BaseModel, Field, field_validator
import asyncio
import os
import traceback

from traj_proxy.utils.validators import validate_run_id, validate_model_name

from traj_proxy.proxy_core.processor import Processor
from traj_proxy.proxy_core.infer_client import InferClient
from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.store.model_repository import ModelRepository
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.store.models import ModelConfig
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import get_sync_max_retries, get_sync_retry_delay, get_sync_fallback_interval, get_models_dir

logger = get_logger(__name__)


# ========== Pydantic 数据模型 ==========

class RegisterModelRequest(BaseModel):
    """注册模型请求"""
    run_id: str = Field(default="", description="运行ID，空字符串表示全局模型")
    model_name: str = Field(..., description="模型名称")
    url: str = Field(..., description="Infer 服务 URL")
    api_key: str = Field(..., description="API 密钥")
    tokenizer_path: Optional[str] = Field(default=None, description="Tokenizer 路径（token_in_token_out=True 时必需）")
    token_in_token_out: bool = Field(default=False, description="是否使用 Token-in-Token-out 模式")
    tool_parser: str = Field(default="", description="Tool parser 名称")
    reasoning_parser: str = Field(default="", description="Reasoning parser 名称")

    @field_validator('run_id')
    @classmethod
    def validate_run_id_field(cls, v):
        """校验 run_id 格式"""
        valid, msg = validate_run_id(v)
        if not valid:
            raise ValueError(msg)
        return v

    @field_validator('model_name')
    @classmethod
    def validate_model_name_field(cls, v):
        """校验 model_name 格式"""
        valid, msg = validate_model_name(v)
        if not valid:
            raise ValueError(msg)
        return v

    @field_validator('tokenizer_path')
    @classmethod
    def validate_tokenizer_path(cls, v, info):
        """校验 tokenizer_path 在 token_in_token_out=True 时必须提供"""
        if info.data.get('token_in_token_out') and not v:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")
        return v


class RegisterModelResponse(BaseModel):
    """注册模型响应"""
    status: str
    run_id: str
    model_name: str
    detail: dict


class DeleteModelResponse(BaseModel):
    """删除模型响应"""
    status: str
    run_id: str
    model_name: str
    deleted: bool


# ========== ProcessorManager 类 ==========

class ProcessorManager:
    """多模型处理器管理器

    管理 (run_id, model_name) 到 Processor 的映射，支持：
    - 动态注册新模型
    - 删除已注册模型
    - 根据 run_id 和 model_name 获取 Processor
    - 从 session_id 解析 run_id 进行路由
    - 列出所有已注册模型
    """

    def __init__(self, db_manager: DatabaseManager, db_url: str = ""):
        """初始化 ProcessorManager

        Args:
            db_manager: 数据库管理器（所有 Processor 共享）
            db_url: 数据库连接 URL（用于 LISTEN/NOTIFY 专用连接）
        """
        self.db_manager = db_manager
        self._db_url = db_url
        # 分离存储：预置模型和动态模型，键为 (run_id, model_name) 元组
        self.config_processors: Dict[Tuple[str, str], Processor] = {}  # 预置模型（不存数据库）
        self.dynamic_processors: Dict[Tuple[str, str], Processor] = {}  # 动态模型（从数据库同步）

        # 模型注册表和同步控制
        self.model_registry = ModelRepository(db_manager.pool)
        self.request_repository = RequestRepository(db_manager.pool)
        self._sync_task: Optional[asyncio.Task] = None
        self._fallback_sync_task: Optional[asyncio.Task] = None
        self._notification_listener = None
        # 从配置读取同步参数
        self._sync_max_retries = get_sync_max_retries()
        self._sync_retry_delay = get_sync_retry_delay()
        self._fallback_interval = get_sync_fallback_interval()

        logger.info(f"ProcessorManager 初始化完成，LISTEN/NOTIFY + 兜底同步间隔: {self._fallback_interval}秒")

    def _create_processor(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: Optional[str] = None,
        token_in_token_out: bool = False,
        run_id: str = "",
        tool_parser: str = "",
        reasoning_parser: str = ""
    ) -> Processor:
        """创建 Processor 实例的工厂方法

        同时创建关联的 StreamingProcessor 实例。

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径（token_in_token_out=True 时必需）
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            run_id: 运行ID，空字符串表示全局模型
            tool_parser: Tool parser 名称
            reasoning_parser: Reasoning parser 名称

        Returns:
            新创建的 Processor 实例（包含 streaming_processor 属性）

        Raises:
            ValueError: 当 token_in_token_out=True 但 tokenizer_path 未提供时
        """
        # 验证参数
        if token_in_token_out and not tokenizer_path:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")

        infer_client = InferClient(
            base_url=url,
            api_key=api_key
        )

        config = {
            "token_in_token_out": token_in_token_out
        }

        processor = Processor(
            model=model_name,
            run_id=run_id,
            tokenizer_path=tokenizer_path,
            request_repository=self.request_repository,
            infer_client=infer_client,
            config=config,
            tool_parser=tool_parser,
            reasoning_parser=reasoning_parser
        )

        # 创建关联的 StreamingProcessor
        from traj_proxy.proxy_core.streaming_processor import StreamingProcessor
        processor.streaming_processor = StreamingProcessor(
            model=model_name,
            tokenizer_path=tokenizer_path,
            prompt_builder=processor.prompt_builder,
            token_builder=processor.token_builder,
            infer_client=infer_client,
            request_repository=self.request_repository,
            tool_parser_name=tool_parser,
            reasoning_parser_name=reasoning_parser
        )

        return processor

    async def start_sync(self):
        """启动模型同步：LISTEN/NOTIFY（主）+ 定期兜底"""
        # 1. 首先执行一次全量同步（初始加载）
        try:
            await self._sync_from_db()
            logger.info("初始全量模型同步完成")
        except Exception as e:
            logger.error(f"初始全量同步失败: {e}")

        # 2. 启动 LISTEN/NOTIFY 监听器（如果配置了 db_url）
        if self._db_url:
            from traj_proxy.store.notification_listener import NotificationListener
            self._notification_listener = NotificationListener(
                db_url=self._db_url,
                on_notification=self._handle_notification,
                reconnect_delay=self._sync_retry_delay,
            )
            await self._notification_listener.start()
            logger.info("LISTEN/NOTIFY 实时同步已激活")
        else:
            logger.warning("未配置 db_url，LISTEN/NOTIFY 已禁用，仅依赖轮询同步")

        # 3. 启动兜底定期全量同步（间隔较长）
        self._fallback_sync_task = asyncio.create_task(
            self._periodic_sync(interval=self._fallback_interval)
        )
        logger.info(f"兜底定期同步已启动，间隔: {self._fallback_interval}秒")

    async def stop_sync(self):
        """停止所有同步任务"""
        if self._notification_listener:
            await self._notification_listener.stop()
            self._notification_listener = None
        if self._fallback_sync_task:
            self._fallback_sync_task.cancel()
            try:
                await self._fallback_sync_task
            except asyncio.CancelledError:
                pass
            self._fallback_sync_task = None
        logger.info("模型同步已停止")

    async def _periodic_sync(self, interval: int = None):
        """定期全量同步（兜底机制，带重试）"""
        interval = interval or self._fallback_interval
        retry_count = 0
        current_retry_delay = self._sync_retry_delay

        while True:
            try:
                await asyncio.sleep(interval)
                await self._sync_from_db()
                logger.debug("兜底同步完成")
                retry_count = 0
                current_retry_delay = self._sync_retry_delay
            except DatabaseError as e:
                retry_count += 1
                if retry_count >= self._sync_max_retries:
                    logger.error(f"兜底同步失败（达到最大重试次数 {self._sync_max_retries}）: {e}")
                    retry_count = 0
                    current_retry_delay = self._sync_retry_delay
                else:
                    delay = current_retry_delay * (2 ** (retry_count - 1))
                    logger.warning(f"兜底同步失败（第 {retry_count}/{self._sync_max_retries} 次），{delay}秒后重试: {e}")
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"兜底同步出现非数据库错误: {e}", exc_info=True)
                await asyncio.sleep(interval)

    async def _handle_notification(self, payload: dict):
        """处理 LISTEN/NOTIFY 通知，执行增量同步

        对于 register：从数据库获取单个模型并更新内存
        对于 unregister：直接从内存移除

        Args:
            payload: 通知内容，包含 action, run_id, model_name, timestamp
        """
        action = payload.get("action")
        run_id = payload.get("run_id", "")
        model_name = payload.get("model_name", "")
        key = (run_id, model_name)

        try:
            if action == "register":
                # 增量查询：仅获取变更的单个模型
                config = await self.model_registry.get_by_key(run_id, model_name)
                if config:
                    self._create_processor_from_model_config(config, target='dynamic')
                    logger.info(f"通知同步: 注册模型 {model_name} (run_id={run_id})")
                else:
                    # 模型未找到，降级到全量同步
                    logger.warning(
                        f"通知同步: register 事件但模型未在 DB 中找到: "
                        f"{model_name} (run_id={run_id})，降级到全量同步"
                    )
                    await self._sync_from_db()

            elif action == "unregister":
                if key in self.dynamic_processors:
                    del self.dynamic_processors[key]
                    logger.info(f"通知同步: 删除模型 {model_name} (run_id={run_id})")
                else:
                    logger.debug(
                        f"通知同步: unregister 事件但模型不在内存中: "
                        f"{model_name} (run_id={run_id})"
                    )
            else:
                logger.warning(f"通知同步: 未知 action '{action}'，忽略")

        except Exception as e:
            logger.error(
                f"通知同步处理失败 (action={action}, model={model_name}, "
                f"run_id={run_id})，降级到全量同步: {e}",
                exc_info=True
            )
            await self._sync_from_db()

    async def _sync_from_db(self):
        """从数据库同步动态模型到内存（预置模型不受影响）"""
        try:
            # 只获取动态模型
            db_dynamic_models = await self.model_registry.get_all()
            db_dynamic_model_keys = {(m.run_id, m.model_name) for m in db_dynamic_models}

            local_dynamic_model_keys = set(self.dynamic_processors.keys())

            # 添加或更新动态模型
            for config in db_dynamic_models:
                key = (config.run_id, config.model_name)
                if key not in self.dynamic_processors:
                    # 新增动态模型
                    self._create_processor_from_model_config(config, target='dynamic')
                else:
                    # 检查是否需要更新（比较所有影响 Processor 的字段）
                    existing = self.dynamic_processors[key]
                    if (existing.tokenizer_path != config.tokenizer_path or
                        existing.token_in_token_out != config.token_in_token_out or
                        existing.infer_client.base_url != config.url or
                        existing.infer_client.api_key != config.api_key or
                        existing.tool_parser_name != config.tool_parser or
                        existing.reasoning_parser_name != config.reasoning_parser):
                        # 配置变化，重新注册
                        self._create_processor_from_model_config(config, target='dynamic')

            # 删除不在数据库中的动态模型
            to_remove = local_dynamic_model_keys - db_dynamic_model_keys
            for key in to_remove:
                del self.dynamic_processors[key]
                logger.info(f"同步删除动态模型: run_id={key[0]}, model_name={key[1]}")

        except Exception as e:
            logger.error(f"从数据库同步动态模型失败: {e}", exc_info=True)
            raise

    def _create_processor_from_model_config(self, config: ModelConfig, target: str = 'dynamic'):
        """从配置创建 Processor（内部方法，不持久化到数据库）

        Args:
            config: 模型配置
            target: 目标字典，'config'（预置模型）或'dynamic'（动态模型）
        """
        processor = self._create_processor(
            model_name=config.model_name,
            url=config.url,
            api_key=config.api_key,
            tokenizer_path=config.tokenizer_path,
            token_in_token_out=config.token_in_token_out,
            run_id=config.run_id,
            tool_parser=config.tool_parser,
            reasoning_parser=config.reasoning_parser
        )

        key = (config.run_id, config.model_name)

        # 根据目标选择字典
        if target == 'config':
            self.config_processors[key] = processor
            logger.info(f"[{config.model_name}] 注册预置模型: run_id={config.run_id}")
        else:
            self.dynamic_processors[key] = processor
            logger.info(f"[{config.model_name}] 注册动态模型: run_id={config.run_id}")

    def _register_processor_impl(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: Optional[str] = None,
        token_in_token_out: bool = False,
        run_id: str = "",
        tool_parser: str = "",
        reasoning_parser: str = ""
    ) -> Tuple[Processor, Tuple[str, str]]:
        """注册处理器的公共逻辑

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            run_id: 运行ID
            tool_parser: Tool parser 名称
            reasoning_parser: Reasoning parser 名称

        Returns:
            (processor, key) 元组

        Raises:
            ValueError: 如果模型已存在或参数无效
        """
        key = (run_id, model_name)

        # 检查是否已存在（包括预置模型）
        if key in self.config_processors or key in self.dynamic_processors:
            raise ValueError(f"模型 '{model_name}' 已存在 (run_id={run_id})")

        # 验证参数
        if token_in_token_out and not tokenizer_path:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")

        # 解析 tokenizer 路径（仅在 token_in_token_out=True 时需要）
        resolved_tokenizer_path = None
        if token_in_token_out and tokenizer_path:
            resolved_tokenizer_path = self._resolve_tokenizer_path(tokenizer_path)

        # 创建 Processor
        processor = self._create_processor(
            model_name=model_name,
            url=url,
            api_key=api_key,
            tokenizer_path=resolved_tokenizer_path,
            token_in_token_out=token_in_token_out,
            run_id=run_id,
            tool_parser=tool_parser,
            reasoning_parser=reasoning_parser
        )

        return processor, key

    async def register_dynamic_processor(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: Optional[str] = None,
        token_in_token_out: bool = False,
        persist_to_db: bool = True,
        run_id: str = "",
        tool_parser: str = "",
        reasoning_parser: str = ""
    ) -> Processor:
        """注册新的 Processor（仅动态模型）

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径（token_in_token_out=True 时必需）
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            persist_to_db: 是否持久化到数据库（默认 True）
            run_id: 运行ID，空字符串表示全局模型
            tool_parser: Tool parser 名称
            reasoning_parser: Reasoning parser 名称

        Returns:
            新创建的 Processor 实例

        Raises:
            ValueError: 如果 (run_id, model_name) 已存在（包括预置模型）或参数无效
            DatabaseError: 数据库操作失败
        """
        # 调用公共注册逻辑
        processor, key = self._register_processor_impl(
            model_name=model_name,
            url=url,
            api_key=api_key,
            tokenizer_path=tokenizer_path,
            token_in_token_out=token_in_token_out,
            run_id=run_id,
            tool_parser=tool_parser,
            reasoning_parser=reasoning_parser
        )

        # 存入 dynamic_processors
        self.dynamic_processors[key] = processor
        logger.info(f"[{model_name}] 注册动态模型成功: run_id={run_id}, url={url}")

        # 持久化到数据库（同步，快速失败）
        if persist_to_db:
            try:
                await self.model_registry.register(
                    model_name=model_name,
                    url=url,
                    api_key=api_key,
                    tokenizer_path=processor.tokenizer_path,
                    token_in_token_out=token_in_token_out,
                    run_id=run_id,
                    tool_parser=tool_parser,
                    reasoning_parser=reasoning_parser
                )
            except Exception as e:
                # 数据库失败时，回滚本地注册
                if key in self.dynamic_processors:
                    del self.dynamic_processors[key]
                logger.error(f"持久化模型到数据库失败: {e}\n{traceback.format_exc()}")
                raise DatabaseError(f"注册模型失败（数据库错误）: {str(e)}")

        return processor

    def register_static_processor(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: Optional[str] = None,
        token_in_token_out: bool = False,
        run_id: str = "",
        tool_parser: str = "",
        reasoning_parser: str = ""
    ) -> Processor:
        """注册预置模型（仅内存，不持久化到数据库）

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径（token_in_token_out=True 时必需）
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            run_id: 运行ID，空字符串表示全局模型
            tool_parser: Tool parser 名称
            reasoning_parser: Reasoning parser 名称

        Returns:
            新创建的 Processor 实例

        Raises:
            ValueError: 如果 (run_id, model_name) 已存在（包括动态模型）或参数无效
        """
        # 调用公共注册逻辑
        processor, key = self._register_processor_impl(
            model_name=model_name,
            url=url,
            api_key=api_key,
            tokenizer_path=tokenizer_path,
            token_in_token_out=token_in_token_out,
            run_id=run_id,
            tool_parser=tool_parser,
            reasoning_parser=reasoning_parser
        )

        # 存入 config_processors
        self.config_processors[key] = processor
        logger.info(f"[{model_name}] 注册预置模型成功: run_id={run_id}")

        return processor

    async def unregister_dynamic_processor(self, model_name: str, persist_to_db: bool = True, run_id: str = "") -> bool:
        """删除已注册的 Processor（优先删除动态模型）

        Args:
            model_name: 模型名称
            persist_to_db: 是否从数据库删除（默认 True）
            run_id: 运行ID，空字符串表示全局模型

        Returns:
            是否成功删除（False 表示模型不存在或为预置模型）

        Raises:
            DatabaseError: 数据库操作失败
        """
        key = (run_id, model_name)

        # 优先从 dynamic_processors 删除
        if key in self.dynamic_processors:
            del self.dynamic_processors[key]
            logger.info(f"[{model_name}] 删除动态模型成功: run_id={run_id}")
            deleted = True
        elif key in self.config_processors:
            # 预置模型不允许通过 API 删除
            logger.warning(f"[{model_name}] 尝试删除预置模型（不允许）: run_id={run_id}")
            return False
        else:
            logger.warning(f"[{model_name}] 尝试删除不存在的模型: run_id={run_id}")
            return False

        # 从数据库删除（同步，快速失败）
        if persist_to_db and deleted:
            try:
                success = await self.model_registry.unregister(model_name, run_id)
                if not success:
                    # 数据库中不存在，记录警告但不抛出异常
                    logger.warning(f"[{model_name}] 数据库中未找到模型: run_id={run_id}")
            except Exception as e:
                logger.error(f"[{model_name}] 从数据库删除模型失败: {e}\n{traceback.format_exc()}")
                raise DatabaseError(f"删除模型失败（数据库错误）: {str(e)}")

        return deleted

    def get_processor(self, run_id: str, model_name: str) -> Optional[Processor]:
        """根据 run_id 和 model_name 获取 Processor（精确匹配）

        查找顺序：
        1. 动态模型精确匹配 (run_id, model_name)
        2. 预置模型精确匹配 (run_id, model_name)

        注意：不再回退到全局预置模型，保持严格隔离

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            Processor 实例，如果不存在则返回 None
        """
        key = (run_id, model_name)
        # 优先查找动态模型
        processor = self.dynamic_processors.get(key)
        if processor:
            return processor
        # 查找预置模型
        return self.config_processors.get(key)

    def get_processor_or_raise(self, run_id: str, model_name: str) -> Processor:
        """根据 run_id 和 model_name 获取 Processor，不存在时抛出异常

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            Processor 实例

        Raises:
            ValueError: 如果模型不存在
        """
        processor = self.get_processor(run_id, model_name)
        if processor is None:
            raise ValueError(f"模型 '{model_name}' 未注册 (run_id={run_id})")
        return processor

    def list_models(self) -> List[Tuple[str, str]]:
        """列出所有已注册的模型（预置 + 动态）

        Returns:
            (run_id, model_name) 元组列表
        """
        all_keys = set(self.config_processors.keys()) | set(self.dynamic_processors.keys())
        return sorted(all_keys)

    def get_processor_info(self, run_id: str, model_name: str) -> Optional[Dict]:
        """获取 Processor 的详细信息

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            包含模型信息的字典，如果不存在则返回 None
        """
        processor = self.get_processor(run_id, model_name)
        if processor is None:
            return None

        return {
            "run_id": run_id,
            "model_name": processor.model,
            "tokenizer_path": processor.tokenizer_path,
            "token_in_token_out": processor.token_in_token_out,
            "infer_client_url": processor.infer_client.base_url if processor.infer_client else None
        }

    def get_all_processors_info(self) -> List[Dict]:
        """获取所有 Processor 的详细信息（预置 + 动态）

        Returns:
            包含所有模型信息的字典列表
        """
        all_keys = set(self.config_processors.keys()) | set(self.dynamic_processors.keys())
        return [
            self.get_processor_info(key[0], key[1])
            for key in all_keys
        ]

    def _resolve_tokenizer_path(self, tokenizer: str) -> str:
        """解析 tokenizer 路径

        Args:
            tokenizer: Tokenizer（本地路径或 HuggingFace 模型名称）

        Returns:
            实际的 tokenizer 路径

        Raises:
            ValueError: 如果 tokenizer 不存在
        """
        # 如果是绝对路径，直接使用
        if os.path.isabs(tokenizer):
            if not os.path.exists(tokenizer):
                raise ValueError(f"Tokenizer 路径不存在: {tokenizer}")
            return tokenizer

        # 检查是否是 HuggingFace 模型名称（包含 /）
        if "/" in tokenizer and not tokenizer.startswith("/"):
            # 先检查本地 models 目录是否存在
            models_dir = get_models_dir()
            local_path = os.path.join(models_dir, tokenizer)
            if os.path.exists(local_path):
                return local_path
            # 本地不存在，返回 HuggingFace 名称，让 AutoTokenizer.from_pretrained 处理
            return tokenizer

        # 相对路径：在 models 目录下查找
        models_dir = get_models_dir()
        local_path = os.path.join(models_dir, tokenizer)

        if os.path.exists(local_path):
            return local_path

        raise ValueError(
            f"Tokenizer '{tokenizer}' 不存在。请使用 download_tokenizer.py 脚本下载，"
            f"或使用绝对路径，或使用 HuggingFace 模型名称（如 'Qwen/Qwen3.5-2B'）。"
            f"已检查目录: {models_dir}"
        )
