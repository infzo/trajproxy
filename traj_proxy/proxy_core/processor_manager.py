"""
ProcessorManager - 多模型处理器管理器

管理多个 Processor 实例，支持动态注册、删除和查询。

职责：
- 管理预置模型和动态模型
- 创建和缓存 Processor 实例
- 提供 Processor 查询接口

注意：模型同步逻辑已移至 ModelSynchronizer
"""

from typing import Dict, Optional, List, Tuple
import os
import traceback

from traj_proxy.proxy_core.processor import Processor
from traj_proxy.proxy_core.infer_client import InferClient
from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.store.model_repository import ModelRepository
from traj_proxy.store.request_repository import RequestRepository
from traj_proxy.store.models import ModelConfig
from traj_proxy.exceptions import DatabaseError
from traj_proxy.utils.logger import get_logger
from traj_proxy.utils.config import get_models_dir

# API 数据模型已移至 schemas 模块
from traj_proxy.serve.schemas import (
    RegisterModelRequest,
    RegisterModelResponse,
    DeleteModelResponse,
)

logger = get_logger(__name__)


# ========== ProcessorManager 类 ==========

class ProcessorManager:
    """多模型处理器管理器

    管理 (run_id, model_name) 到 Processor 的映射，支持：
    - 动态注册新模型
    - 删除已注册模型
    - 根据 run_id 和 model_name 获取 Processor
    - 列出所有已注册模型

    注意：模型同步由 ModelSynchronizer 负责
    """

    def __init__(self, db_manager: DatabaseManager):
        """初始化 ProcessorManager

        Args:
            db_manager: 数据库管理器（所有 Processor 共享）
        """
        self.db_manager = db_manager
        # 分离存储：预置模型和动态模型，键为 (run_id, model_name) 元组
        self.config_processors: Dict[Tuple[str, str], Processor] = {}  # 预置模型（不存数据库）
        self.dynamic_processors: Dict[Tuple[str, str], Processor] = {}  # 动态模型（从数据库同步）

        # 模型注册表（供 ModelSynchronizer 使用）
        self.model_registry = ModelRepository(db_manager.pool)
        self.request_repository = RequestRepository(db_manager.pool)

        logger.info("ProcessorManager 初始化完成")

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

        新架构：Processor 内部使用 Pipeline 处理请求，
        不再需要单独的 StreamingProcessor。

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

        return processor

    # ========== ModelSynchronizer 回调接口 ==========

    async def register_from_config(self, config: ModelConfig):
        """从配置注册模型（供 ModelSynchronizer 调用）

        Args:
            config: 模型配置
        """
        self._create_processor_from_model_config(config, target='dynamic')

    async def unregister_by_key(self, key: Tuple[str, str]):
        """根据 key 删除模型（供 ModelSynchronizer 调用）

        Args:
            key: (run_id, model_name) 元组
        """
        if key in self.dynamic_processors:
            del self.dynamic_processors[key]

    async def full_sync(self, db_models: List[ModelConfig]):
        """全量同步动态模型（供 ModelSynchronizer 调用）

        Args:
            db_models: 数据库中的所有动态模型配置
        """
        db_model_keys = {(m.run_id, m.model_name) for m in db_models}
        local_model_keys = set(self.dynamic_processors.keys())

        # 添加或更新模型
        for config in db_models:
            key = (config.run_id, config.model_name)
            if key not in self.dynamic_processors:
                # 新增模型
                self._create_processor_from_model_config(config, target='dynamic')
            else:
                # 检查是否需要更新
                existing = self.dynamic_processors[key]
                if (existing.tokenizer_path != config.tokenizer_path or
                    existing.token_in_token_out != config.token_in_token_out or
                    existing.infer_client.base_url != config.url or
                    existing.infer_client.api_key != config.api_key or
                    existing.tool_parser_name != config.tool_parser or
                    existing.reasoning_parser_name != config.reasoning_parser):
                    # 配置变化，重新注册
                    self._create_processor_from_model_config(config, target='dynamic')

        # 删除不在数据库中的模型
        to_remove = local_model_keys - db_model_keys
        for key in to_remove:
            del self.dynamic_processors[key]
            logger.info(f"同步删除动态模型: run_id={key[0]}, model_name={key[1]}")

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

    async def try_get_or_sync_from_db(
        self, run_id: str, model_name: str
    ) -> Optional[Processor]:
        """从数据库查询并同步模型（用于缓存未命中时的回退）

        在多 Worker 环境下，LISTEN/NOTIFY 通知可能存在延迟，
        导致请求到达某 Worker 时模型尚未同步到本地内存。
        此方法在本地未找到模型时，回退查询数据库并同步。

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            Processor 实例，如果不存在则返回 None
        """
        key = (run_id, model_name)

        # 1. 先检查本地是否已有（可能刚通过 NOTIFY 同步）
        processor = self.dynamic_processors.get(key)
        if processor:
            return processor

        # 2. 查询数据库
        try:
            config = await self.model_registry.get_by_key(run_id, model_name)
            if config is None:
                logger.debug(f"DB 回退查询: 模型 {model_name} (run_id={run_id}) 不存在于数据库")
                return None

            # 3. 同步到本地
            await self.register_from_config(config)
            logger.info(f"DB 回退同步: 从数据库同步模型 {model_name} (run_id={run_id})")

            # 4. 返回 processor
            return self.dynamic_processors.get(key)

        except Exception as e:
            logger.error(f"DB 回退查询失败: {e}")
            return None

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
