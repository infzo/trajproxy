"""
ProcessorManager - 多模型处理器管理器

管理多个 Processor 实例，支持动态注册、删除和查询。

职责：
- 管理预置模型和动态模型
- 懒加载 + LRU 缓存 Processor 实例
- 提供 Processor 查询接口

注意：模型同步逻辑已移至 ModelSynchronizer
"""

import asyncio
from collections import OrderedDict
from typing import Dict, Optional, List, Tuple
from datetime import datetime
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
from traj_proxy.utils.config import get_models_dir, get_infer_client_config, get_processor_cache_max_size

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

    处理器采用懒加载策略：注册时仅存储轻量 ModelConfig，
    首次请求时才创建 Processor 实例。通过 LRU 缓存管理
    内存中的 Processor 数量。

    注意：模型同步由 ModelSynchronizer 负责
    """

    def __init__(self, db_manager: DatabaseManager):
        """初始化 ProcessorManager

        Args:
            db_manager: 数据库管理器（所有 Processor 共享）
        """
        self.db_manager = db_manager

        # 轻量配置存储（不创建 Processor）
        self._config_configs: Dict[Tuple[str, str], ModelConfig] = {}   # 预置模型（来自 config YAML）
        self._dynamic_configs: Dict[Tuple[str, str], ModelConfig] = {}  # 动态模型（来自 DB 同步）

        # LRU 缓存：存储已加载的 Processor 实例
        self._processor_cache: OrderedDict[Tuple[str, str], Processor] = OrderedDict()
        self._cache_max_size: int = get_processor_cache_max_size()

        # 防止并发加载同一模型的锁
        self._load_lock = asyncio.Lock()

        # 模型注册表（供 ModelSynchronizer 使用）
        self.model_registry = ModelRepository(db_manager.pool)
        self.request_repository = RequestRepository(db_manager.pool)

        logger.info(f"ProcessorManager 初始化完成，LRU 缓存上限: {self._cache_max_size}")

    @property
    def config_processor_count(self) -> int:
        """预置模型注册数"""
        return len(self._config_configs)

    @property
    def dynamic_processor_count(self) -> int:
        """动态模型注册数"""
        return len(self._dynamic_configs)

    def _create_processor(
        self,
        model_name: str,
        url: str,
        api_key: str,
        tokenizer_path: Optional[str] = None,
        token_in_token_out: bool = False,
        run_id: str = "",
        tool_parser: str = "",
        reasoning_parser: str = "",
        updated_at: Optional[datetime] = None
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
            updated_at: 模型注册/更新时间

        Returns:
            新创建的 Processor 实例

        Raises:
            ValueError: 当 token_in_token_out=True 但 tokenizer_path 未提供时
        """
        # 验证参数
        if token_in_token_out and not tokenizer_path:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")

        # 获取 InferClient 超时配置
        infer_config = get_infer_client_config()

        infer_client = InferClient(
            base_url=url,
            api_key=api_key,
            timeout=infer_config.get("read_timeout", 600),
            connect_timeout=infer_config.get("connect_timeout", 60),
            max_connections=infer_config.get("max_connections", 1000)
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
            reasoning_parser=reasoning_parser,
            updated_at=updated_at
        )

        return processor

    # ========== LRU 缓存管理 ==========

    def _touch(self, key: Tuple[str, str]) -> None:
        """标记 key 为最近使用"""
        if key in self._processor_cache:
            self._processor_cache.move_to_end(key)

    def _evict_one(self) -> None:
        """淘汰最久未使用的 Processor"""
        if self._processor_cache:
            evicted_key, _ = self._processor_cache.popitem(last=False)
            logger.info(
                f"LRU 淘汰: run_id={evicted_key[0]}, model_name={evicted_key[1]}, "
                f"cache_size={len(self._processor_cache)}"
            )

    def _build_processor(self, config: ModelConfig) -> Processor:
        """从 ModelConfig 创建 Processor（不存储）

        Args:
            config: 模型配置

        Returns:
            新创建的 Processor 实例
        """
        return self._create_processor(
            model_name=config.model_name,
            url=config.url,
            api_key=config.api_key,
            tokenizer_path=config.tokenizer_path,
            token_in_token_out=config.token_in_token_out,
            run_id=config.run_id,
            tool_parser=config.tool_parser,
            reasoning_parser=config.reasoning_parser,
            updated_at=config.updated_at
        )

    def _sync_load_processor(self, key: Tuple[str, str], config: ModelConfig) -> Optional[Processor]:
        """同步加载 Processor（无锁版本）

        适用于同步方法中的首次加载。
        """
        while len(self._processor_cache) >= self._cache_max_size:
            self._evict_one()

        try:
            processor = self._build_processor(config)
        except Exception as e:
            logger.error(f"创建 Processor 失败 [{key}]: {e}\n{traceback.format_exc()}")
            return None

        self._processor_cache[key] = processor
        logger.info(
            f"Processor 加载到缓存: run_id={key[0]}, model_name={key[1]}, "
            f"cache_size={len(self._processor_cache)}/{self._cache_max_size}"
        )
        return processor

    async def _load_processor(self, key: Tuple[str, str]) -> Optional[Processor]:
        """异步加载 Processor 到 LRU 缓存（带锁保护）

        防止并发请求同一未加载模型时重复创建。

        Args:
            key: (run_id, model_name) 元组

        Returns:
            已加载的 Processor，或 None（如果 config 不存在）
        """
        # 快速路径：已经在缓存中
        if key in self._processor_cache:
            self._touch(key)
            return self._processor_cache[key]

        # 查找配置
        config = self._config_configs.get(key) or self._dynamic_configs.get(key)
        if config is None:
            return None

        async with self._load_lock:
            # 双重检查：锁内再次确认
            if key in self._processor_cache:
                self._touch(key)
                return self._processor_cache[key]

            # 淘汰旧项（如有必要）
            while len(self._processor_cache) >= self._cache_max_size:
                self._evict_one()

            # 创建 Processor
            try:
                processor = self._build_processor(config)
            except Exception as e:
                logger.error(f"创建 Processor 失败 [{key}]: {e}\n{traceback.format_exc()}")
                return None

            self._processor_cache[key] = processor
            logger.info(
                f"Processor 加载到缓存: run_id={key[0]}, model_name={key[1]}, "
                f"cache_size={len(self._processor_cache)}/{self._cache_max_size}"
            )
            return processor

    # ========== ModelSynchronizer 回调接口 ==========

    async def register_from_config(self, config: ModelConfig):
        """从配置注册模型（供 ModelSynchronizer 调用）

        仅存储轻量配置，不创建 Processor。

        Args:
            config: 模型配置
        """
        key = (config.run_id, config.model_name)
        self._dynamic_configs[key] = config
        logger.debug(f"配置已存储（同步回调）: {key}")

    async def unregister_by_key(self, key: Tuple[str, str]):
        """根据 key 删除模型（供 ModelSynchronizer 调用）

        Args:
            key: (run_id, model_name) 元组
        """
        self._dynamic_configs.pop(key, None)
        self._processor_cache.pop(key, None)
        logger.info(f"模型已注销: run_id={key[0]}, model_name={key[1]}")

    async def full_sync(self, db_models: List[ModelConfig]):
        """全量同步动态模型（供 ModelSynchronizer 调用）

        只同步轻量 ModelConfig，不创建 Processor。
        配置变更时淘汰缓存中的旧 Processor。

        Args:
            db_models: 数据库中的所有动态模型配置
        """
        db_model_keys = {(m.run_id, m.model_name) for m in db_models}
        local_model_keys = set(self._dynamic_configs.keys())

        # 添加或更新模型
        for config in db_models:
            key = (config.run_id, config.model_name)
            existing = self._dynamic_configs.get(key)
            if existing is None:
                # 新增模型
                self._dynamic_configs[key] = config
                logger.info(f"全量同步 - 新增配置: {key}")
            else:
                # 检查配置是否变更
                if (existing.tokenizer_path != config.tokenizer_path or
                    existing.token_in_token_out != config.token_in_token_out or
                    existing.url != config.url or
                    existing.api_key != config.api_key or
                    existing.tool_parser != config.tool_parser or
                    existing.reasoning_parser != config.reasoning_parser):
                    # 配置变更，更新 config 并淘汰旧 Processor
                    self._dynamic_configs[key] = config
                    self._processor_cache.pop(key, None)
                    logger.info(f"全量同步 - 配置变更，缓存已失效: {key}")

        # 删除不在数据库中的模型
        to_remove = local_model_keys - db_model_keys
        for key in to_remove:
            self._dynamic_configs.pop(key, None)
            self._processor_cache.pop(key, None)
            logger.info(f"全量同步 - 已删除: run_id={key[0]}, model_name={key[1]}")

    # ========== 公开注册接口 ==========

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
    ) -> None:
        """注册预置模型（仅内存，不持久化到数据库）

        Processor 在首次请求时懒加载。

        Args:
            model_name: 模型名称
            url: Infer 服务 URL
            api_key: API 密钥
            tokenizer_path: Tokenizer 路径（token_in_token_out=True 时必需）
            token_in_token_out: 是否使用 Token-in-Token-out 模式
            run_id: 运行ID，空字符串表示全局模型
            tool_parser: Tool parser 名称
            reasoning_parser: Reasoning parser 名称

        Raises:
            ValueError: 如果 (run_id, model_name) 已存在或参数无效
        """
        key = (run_id, model_name)

        # 检查重复
        if key in self._config_configs or key in self._dynamic_configs:
            raise ValueError(f"模型 '{model_name}' 已存在 (run_id={run_id})")

        # 验证参数
        if token_in_token_out and not tokenizer_path:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")

        # 构建并存储配置
        config = ModelConfig(
            model_name=model_name,
            url=url,
            api_key=api_key,
            tokenizer_path=tokenizer_path,
            token_in_token_out=token_in_token_out,
            run_id=run_id,
            tool_parser=tool_parser,
            reasoning_parser=reasoning_parser,
            updated_at=datetime.now()
        )
        self._config_configs[key] = config
        logger.info(f"[{model_name}] 注册预置模型成功: run_id={run_id}")

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
    ) -> ModelConfig:
        """注册新的动态模型

        仅存储配置并持久化到 DB，Processor 在首次请求时懒加载。

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
            存储的 ModelConfig

        Raises:
            ValueError: 如果 (run_id, model_name) 已存在或参数无效
            DatabaseError: 数据库操作失败
        """
        key = (run_id, model_name)

        # 检查重复
        if key in self._config_configs or key in self._dynamic_configs:
            raise ValueError(f"模型 '{model_name}' 已存在 (run_id={run_id})")

        # 验证参数
        if token_in_token_out and not tokenizer_path:
            raise ValueError("token_in_token_out=True 时，tokenizer_path 必须提供")

        # 构建配置
        config = ModelConfig(
            model_name=model_name,
            url=url,
            api_key=api_key,
            tokenizer_path=tokenizer_path,
            token_in_token_out=token_in_token_out,
            run_id=run_id,
            tool_parser=tool_parser,
            reasoning_parser=reasoning_parser,
            updated_at=datetime.now()
        )

        # 存储配置
        self._dynamic_configs[key] = config
        logger.info(f"[{model_name}] 注册动态模型成功: run_id={run_id}, url={url}")

        # 持久化到数据库（同步，快速失败）
        if persist_to_db:
            try:
                await self.model_registry.register(
                    model_name=model_name,
                    url=url,
                    api_key=api_key,
                    tokenizer_path=tokenizer_path,
                    token_in_token_out=token_in_token_out,
                    run_id=run_id,
                    tool_parser=tool_parser,
                    reasoning_parser=reasoning_parser
                )
            except Exception as e:
                # 数据库失败时，回滚本地注册
                del self._dynamic_configs[key]
                logger.error(f"持久化模型到数据库失败: {e}\n{traceback.format_exc()}")
                raise DatabaseError(f"注册模型失败（数据库错误）: {str(e)}")

        return config

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

        # 优先从 dynamic_configs 删除
        if key in self._dynamic_configs:
            del self._dynamic_configs[key]
            self._processor_cache.pop(key, None)
            logger.info(f"[{model_name}] 删除动态模型成功: run_id={run_id}")
            deleted = True
        elif key in self._config_configs:
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
                    logger.warning(f"[{model_name}] 数据库中未找到模型: run_id={run_id}")
            except Exception as e:
                logger.error(f"[{model_name}] 从数据库删除模型失败: {e}\n{traceback.format_exc()}")
                raise DatabaseError(f"删除模型失败（数据库错误）: {str(e)}")

        return deleted

    # ========== Processor 查询接口 ==========

    def get_processor(self, run_id: str, model_name: str) -> Optional[Processor]:
        """根据 run_id 和 model_name 获取 Processor（同步，兼容旧接口）

        查找顺序：
        1. LRU 缓存（命中则刷新使用时间）
        2. 配置字典（命中则懒加载）

        注意：异步上下文中请优先使用 get_processor_async()。

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            Processor 实例，如果不存在则返回 None
        """
        key = (run_id, model_name)

        # 检查缓存
        if key in self._processor_cache:
            self._touch(key)
            return self._processor_cache[key]

        # 查找配置
        config = self._config_configs.get(key) or self._dynamic_configs.get(key)
        if config is None:
            return None

        # 懒加载
        return self._sync_load_processor(key, config)

    async def get_processor_async(self, run_id: str, model_name: str) -> Optional[Processor]:
        """根据 run_id 和 model_name 获取 Processor（异步，带锁保护）

        优先使用此方法。首次请求时懒加载，缓存满时自动淘汰。

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            Processor 实例，如果不存在则返回 None
        """
        key = (run_id, model_name)

        # 检查缓存
        if key in self._processor_cache:
            self._touch(key)
            return self._processor_cache[key]

        # 懒加载（带锁保护）
        return await self._load_processor(key)

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

        # 1. 先检查是否已在缓存中
        if key in self._processor_cache:
            self._touch(key)
            return self._processor_cache[key]

        # 2. 检查本地配置（可能已通过 NOTIFY 同步）
        config = self._config_configs.get(key) or self._dynamic_configs.get(key)
        if config is not None:
            return await self._load_processor(key)

        # 3. 查询数据库
        try:
            config = await self.model_registry.get_by_key(run_id, model_name)
            if config is None:
                logger.debug(f"DB 回退查询: 模型 {model_name} (run_id={run_id}) 不存在于数据库")
                return None

            # 存储配置
            self._dynamic_configs[key] = config
            logger.info(f"DB 回退同步: 从数据库同步模型 {model_name} (run_id={run_id})")

            # 懒加载 Processor
            return await self._load_processor(key)

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

        只遍历配置，不触发 Processor 加载。

        Returns:
            (run_id, model_name) 元组列表
        """
        all_keys = set(self._config_configs.keys()) | set(self._dynamic_configs.keys())
        return sorted(all_keys)

    def get_processor_info(self, run_id: str, model_name: str) -> Optional[Dict]:
        """获取 Processor 的详细信息

        从配置读取，不触发懒加载。

        Args:
            run_id: 运行ID
            model_name: 模型名称

        Returns:
            包含模型信息的字典，如果不存在则返回 None
        """
        key = (run_id, model_name)
        config = self._config_configs.get(key) or self._dynamic_configs.get(key)
        if config is None:
            return None

        processor = self._processor_cache.get(key)

        return {
            "run_id": config.run_id,
            "model_name": config.model_name,
            "tokenizer_path": config.tokenizer_path,
            "token_in_token_out": config.token_in_token_out,
            "infer_client_url": config.url if processor is None else (processor.infer_client.base_url if processor.infer_client else None),
            "updated_at": config.updated_at.isoformat() if config.updated_at else None,
            "loaded": processor is not None,
        }

    def get_all_processors_info(self) -> List[Dict]:
        """获取所有 Processor 的详细信息（预置 + 动态）"""
        all_keys = set(self._config_configs.keys()) | set(self._dynamic_configs.keys())
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
