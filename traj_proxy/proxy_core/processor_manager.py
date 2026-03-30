"""
ProcessorManager - 多模型处理器管理器

管理多个 Processor 实例，支持动态注册、删除和查询。
"""

from typing import Dict, Optional, List, Tuple
from pydantic import BaseModel, Field
import asyncio
import os

from traj_proxy.proxy_core.processor import Processor
from traj_proxy.proxy_core.infer_client import InferClient
from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.store.model_repository import ModelRepository
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.store.models import ModelConfig
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import get_sync_interval, get_sync_max_retries, get_sync_retry_delay

logger = get_logger(__name__)


# ========== Pydantic 数据模型 ==========

class RegisterModelRequest(BaseModel):
    """注册模型请求"""
    job_id: str = Field(default="", description="作业ID，空字符串表示全局模型")
    model_name: str = Field(..., description="模型名称")
    url: str = Field(..., description="Infer 服务 URL")
    api_key: str = Field(..., description="API 密钥")
    tokenizer_path: str = Field(..., description="Tokenizer 路径（本地路径或 HuggingFace 模型名称）")
    token_in_token_out: bool = Field(default=False, description="是否使用 Token-in-Token-out 模式")


class RegisterModelResponse(BaseModel):
    """注册模型响应"""
    status: str
    job_id: str
    model_name: str
    detail: dict


class DeleteModelResponse(BaseModel):
    """删除模型响应"""
    status: str
    job_id: str
    model_name: str
    deleted: bool


class ModelInfo(BaseModel):
    """单个模型信息"""
    id: str = Field(..., description="模型 ID")
    object: str = Field(default="model", description="对象类型")
    created: int = Field(default=1677610602, description="创建时间戳")
    owned_by: str = Field(default="organization-owner", description="所有者")


class ListModelsResponse(BaseModel):
    """列出模型响应"""
    object: str = "list"
    data: List[ModelInfo]


# ========== ProcessorManager 类 ==========

class ProcessorManager:
    """多模型处理器管理器

    管理 (job_id, model_name) 到 Processor 的映射，支持：
    - 动态注册新模型
    - 删除已注册模型
    - 根据 job_id 和 model_name 获取 Processor
    - 从 session_id 解析 job_id 进行路由
    - 列出所有已注册模型
    """

    def __init__(self, db_manager: DatabaseManager):
        """初始化 ProcessorManager

        Args:
            db_manager: 数据库管理器（所有 Processor 共享）
        """
        self.db_manager = db_manager
        # 分离存储：预置模型和动态模型，键为 (job_id, model_name) 元组
        self.config_processors: Dict[Tuple[str, str], Processor] = {}  # 预置模型（不存数据库）
        self.dynamic_processors: Dict[Tuple[str, str], Processor] = {}  # 动态模型（从数据库同步）

        # 模型注册表和同步控制
        self.model_registry = ModelRepository(db_manager.pool)
        self.request_repository = RequestRepository(db_manager.pool)
        self._sync_task: Optional[asyncio.Task] = None
        # 从配置读取同步参数
        self._sync_interval = get_sync_interval()
        self._sync_max_retries = get_sync_max_retries()
        self._sync_retry_delay = get_sync_retry_delay()

        logger.info(f"ProcessorManager 初始化完成，同步间隔: {self._sync_interval}秒")

    def _create_processor(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: str,
        token_in_token_out: bool = False,
        job_id: str = ""
    ) -> Processor:
        """创建 Processor 实例的工厂方法

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            job_id: 作业ID，空字符串表示全局模型

        Returns:
            新创建的 Processor 实例
        """
        infer_client = InferClient(
            base_url=url,
            api_key=api_key
        )

        config = {
            "token_in_token_out": token_in_token_out
        }

        processor = Processor(
            model=model_name,
            job_id=job_id,
            tokenizer_path=tokenizer_path,
            request_repository=self.request_repository,
            infer_client=infer_client,
            config=config
        )

        return processor

    async def start_sync(self):
        """启动模型同步（定时轮询）"""
        self._sync_task = asyncio.create_task(self._periodic_sync())
        logger.info(f"模型同步已启动，轮询间隔: {self._sync_interval}秒")

    async def stop_sync(self):
        """停止模型同步"""
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        logger.info("模型同步已停止")

    async def _periodic_sync(self):
        """定期从数据库同步模型配置（带重试机制）"""
        retry_count = 0
        current_retry_delay = self._sync_retry_delay

        while True:
            try:
                await asyncio.sleep(self._sync_interval)
                await self._sync_from_db()
                logger.debug("模型同步完成")
                # 成功后重置重试计数
                retry_count = 0
                current_retry_delay = self._sync_retry_delay
            except DatabaseError as e:
                retry_count += 1
                if retry_count >= self._sync_max_retries:
                    logger.error(f"模型同步失败（达到最大重试次数 {self._sync_max_retries}）: {e}")
                    # 重置重试计数，等待下一轮
                    retry_count = 0
                    current_retry_delay = self._sync_retry_delay
                else:
                    # 指数退避
                    delay = current_retry_delay * (2 ** (retry_count - 1))
                    logger.warning(f"模型同步失败（第 {retry_count}/{self._sync_max_retries} 次），{delay}秒后重试: {e}")
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"模型同步出现非数据库错误: {e}")
                await asyncio.sleep(self._sync_interval)

    async def _sync_from_db(self):
        """从数据库同步动态模型到内存（预置模型不受影响）"""
        try:
            # 只获取动态模型
            db_dynamic_models = await self.model_registry.get_all()
            db_dynamic_model_keys = {(m.job_id, m.model_name) for m in db_dynamic_models}

            local_dynamic_model_keys = set(self.dynamic_processors.keys())

            # 添加或更新动态模型
            for config in db_dynamic_models:
                key = (config.job_id, config.model_name)
                if key not in self.dynamic_processors:
                    # 新增动态模型
                    self._create_processor_from_model_config(config, target='dynamic')
                else:
                    # 检查是否需要更新
                    existing = self.dynamic_processors[key]
                    if (existing.tokenizer_path != config.tokenizer_path or
                        existing.token_in_token_out != config.token_in_token_out):
                        # 配置变化，重新注册
                        self._create_processor_from_model_config(config, target='dynamic')

            # 删除不在数据库中的动态模型
            to_remove = local_dynamic_model_keys - db_dynamic_model_keys
            for key in to_remove:
                del self.dynamic_processors[key]
                logger.info(f"同步删除动态模型: job_id={key[0]}, model_name={key[1]}")

        except Exception as e:
            logger.error(f"从数据库同步动态模型失败: {e}")
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
            job_id=config.job_id
        )

        key = (config.job_id, config.model_name)

        # 根据目标选择字典
        if target == 'config':
            self.config_processors[key] = processor
            logger.info(f"注册预置模型: job_id={config.job_id}, model_name={config.model_name}")
        else:
            self.dynamic_processors[key] = processor
            logger.info(f"注册动态模型: job_id={config.job_id}, model_name={config.model_name}")

    async def register_dynamic_processor(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: str,
        token_in_token_out: bool = False,
        persist_to_db: bool = True,
        job_id: str = ""
    ) -> Processor:
        """注册新的 Processor（仅动态模型）

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径（本地路径或 HuggingFace 模型名称）
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            persist_to_db: 是否持久化到数据库（默认 True）
            job_id: 作业ID，空字符串表示全局模型

        Returns:
            新创建的 Processor 实例

        Raises:
            ValueError: 如果 (job_id, model_name) 已存在（包括预置模型）或 tokenizer 不存在
            DatabaseError: 数据库操作失败
        """
        key = (job_id, model_name)

        # 检查是否已存在（包括预置模型）
        if key in self.config_processors or key in self.dynamic_processors:
            raise ValueError(f"模型 '{model_name}' 已存在 (job_id={job_id})")

        # 解析 tokenizer 路径
        resolved_tokenizer_path = self._resolve_tokenizer_path(tokenizer_path)

        # 创建 Processor
        processor = self._create_processor(
            model_name=model_name,
            url=url,
            api_key=api_key,
            tokenizer_path=resolved_tokenizer_path,
            token_in_token_out=token_in_token_out,
            job_id=job_id
        )

        # 只存入 dynamic_processors
        self.dynamic_processors[key] = processor
        logger.info(f"注册动态模型成功: job_id={job_id}, model_name={model_name}, url={url}, tokenizer={resolved_tokenizer_path}, token_in_token_out={token_in_token_out}")

        # 持久化到数据库（同步，快速失败）
        if persist_to_db:
            try:
                await self.model_registry.register(
                    model_name=model_name,
                    url=url,
                    api_key=api_key,
                    tokenizer_path=resolved_tokenizer_path,
                    token_in_token_out=token_in_token_out,
                    job_id=job_id
                )
            except Exception as e:
                # 数据库失败时，回滚本地注册
                if key in self.dynamic_processors:
                    del self.dynamic_processors[key]
                logger.error(f"持久化模型到数据库失败: {e}")
                raise DatabaseError(f"注册模型失败（数据库错误）: {str(e)}")

        return processor

    def register_static_processor(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: str,
        token_in_token_out: bool = False,
        job_id: str = ""
    ) -> Processor:
        """注册预置模型（仅内存，不持久化到数据库）

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径（本地路径或 HuggingFace 模型名称）
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            job_id: 作业ID，空字符串表示全局模型

        Returns:
            新创建的 Processor 实例

        Raises:
            ValueError: 如果 (job_id, model_name) 已存在（包括动态模型）或 tokenizer 不存在
        """
        key = (job_id, model_name)

        # 检查是否已存在（包括动态模型）
        if key in self.config_processors or key in self.dynamic_processors:
            raise ValueError(f"模型 '{model_name}' 已存在 (job_id={job_id})")

        # 解析 tokenizer 路径
        resolved_tokenizer_path = self._resolve_tokenizer_path(tokenizer_path)

        # 创建 Processor
        processor = self._create_processor(
            model_name=model_name,
            url=url,
            api_key=api_key,
            tokenizer_path=resolved_tokenizer_path,
            token_in_token_out=token_in_token_out,
            job_id=job_id
        )

        # 只存入 config_processors
        self.config_processors[key] = processor
        logger.info(f"注册预置模型成功: job_id={job_id}, model_name={model_name}（不持久化到数据库）")

        return processor

    async def unregister_dynamic_processor(self, model_name: str, persist_to_db: bool = True, job_id: str = "") -> bool:
        """删除已注册的 Processor（优先删除动态模型）

        Args:
            model_name: 模型名称
            persist_to_db: 是否从数据库删除（默认 True）
            job_id: 作业ID，空字符串表示全局模型

        Returns:
            是否成功删除（False 表示模型不存在或为预置模型）

        Raises:
            DatabaseError: 数据库操作失败
        """
        key = (job_id, model_name)

        # 优先从 dynamic_processors 删除
        if key in self.dynamic_processors:
            del self.dynamic_processors[key]
            logger.info(f"删除动态模型成功: job_id={job_id}, model_name={model_name}")
            deleted = True
        elif key in self.config_processors:
            # 预置模型不允许通过 API 删除
            logger.warning(f"尝试删除预置模型（不允许）: job_id={job_id}, model_name={model_name}")
            return False
        else:
            logger.warning(f"尝试删除不存在的模型: job_id={job_id}, model_name={model_name}")
            return False

        # 从数据库删除（同步，快速失败）
        if persist_to_db and deleted:
            try:
                success = await self.model_registry.unregister(model_name, job_id)
                if not success:
                    # 数据库中不存在，记录警告但不抛出异常
                    logger.warning(f"数据库中未找到模型: job_id={job_id}, model_name={model_name}")
            except Exception as e:
                logger.error(f"从数据库删除模型失败: {e}")
                raise DatabaseError(f"删除模型失败（数据库错误）: {str(e)}")

        return deleted

    def get_processor(self, job_id: str, model_name: str) -> Optional[Processor]:
        """根据 job_id 和 model_name 获取 Processor（优先返回动态模型）

        Args:
            job_id: 作业ID
            model_name: 模型名称

        Returns:
            Processor 实例，如果不存在则返回 None
        """
        key = (job_id, model_name)
        return self.dynamic_processors.get(key) or self.config_processors.get(key)

    def get_processor_by_session(self, model_name: str, session_id: str) -> Optional[Processor]:
        """根据 session_id 和 model_name 获取 Processor

        Args:
            model_name: 模型名称
            session_id: 会话ID，格式为 {job_id}#{sample_id}#{task_id}

        Returns:
            Processor 实例，如果不存在则返回 None
        """
        job_id = self._extract_job_id(session_id)
        return self.get_processor(job_id, model_name)

    def _extract_job_id(self, session_id: str) -> str:
        """从 session_id 提取作业ID

        Args:
            session_id: 会话ID，格式为 {job_id}#{sample_id}#{task_id}

        Returns:
            作业ID
        """
        if session_id and '#' in session_id:
            return session_id.split('#')[0]
        return ""

    def get_processor_or_raise(self, job_id: str, model_name: str) -> Processor:
        """根据 job_id 和 model_name 获取 Processor，不存在时抛出异常

        Args:
            job_id: 作业ID
            model_name: 模型名称

        Returns:
            Processor 实例

        Raises:
            ValueError: 如果模型不存在
        """
        processor = self.get_processor(job_id, model_name)
        if processor is None:
            raise ValueError(f"模型 '{model_name}' 未注册 (job_id={job_id})")
        return processor

    def list_models(self) -> List[Tuple[str, str]]:
        """列出所有已注册的模型（预置 + 动态）

        Returns:
            (job_id, model_name) 元组列表
        """
        all_keys = set(self.config_processors.keys()) | set(self.dynamic_processors.keys())
        return sorted(all_keys)

    def get_processor_info(self, job_id: str, model_name: str) -> Optional[Dict]:
        """获取 Processor 的详细信息

        Args:
            job_id: 作业ID
            model_name: 模型名称

        Returns:
            包含模型信息的字典，如果不存在则返回 None
        """
        processor = self.get_processor(job_id, model_name)
        if processor is None:
            return None

        return {
            "job_id": job_id,
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
            tokenizer: Tokenizer（路径或 HF 模型名称）

        Returns:
            实际的 tokenizer 路径

        Raises:
            ValueError: 如果 tokenizer 不存在
        """
        # 如果是路径，直接使用
        if os.path.sep in tokenizer or os.path.isabs(tokenizer):
            if not os.path.exists(tokenizer):
                raise ValueError(f"Tokenizer 路径不存在: {tokenizer}")
            return tokenizer

        # 检查 models 目录
        models_dir = os.path.join(os.path.dirname(__file__), "..", "..", "models")
        local_path = os.path.join(models_dir, tokenizer)

        if os.path.exists(local_path):
            return local_path

        raise ValueError(
            f"Tokenizer '{tokenizer}' 不存在。请使用 download_tokenizer.py 脚本下载，"
            f"或使用绝对路径。已检查目录: {models_dir}"
        )
