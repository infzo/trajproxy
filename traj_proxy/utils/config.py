"""
配置管理模块

统一的配置加载和访问入口
"""
from pathlib import Path
import os
import yaml
from typing import Dict, Optional

_config: Optional[Dict] = None


def get_config_path(config_name: str = "config.yaml") -> Path:
    """返回统一的配置文件路径，允许环境变量覆盖。"""
    env_path = os.getenv("TRAJ_PROXY_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()

    # 从 traj_proxy/ 向上一级到项目根目录，然后进入 configs/
    return Path(__file__).resolve().parents[1] / "configs" / config_name


def load_yaml_config(config_name: str = "config.yaml") -> dict:
    """加载 YAML 配置。"""
    config_path = get_config_path(config_name)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(config_path: str = "config.yaml") -> Dict:
    """
    加载配置文件（缓存版本）

    参数:
        config_path: 配置文件路径（相对于项目根目录）

    返回:
        配置字典
    """
    global _config
    if _config is None:
        _config = load_yaml_config(config_path)
    return _config


def get_config() -> Dict:
    """
    获取已加载的配置

    返回:
        配置字典
    """
    if _config is None:
        return load_config()
    return _config


def get_proxy_workers_config() -> Dict:
    """
    获取 proxy_workers 配置

    返回:
        proxy_workers 配置字典
    """
    return get_config().get("proxy_workers", {})


def get_database_config() -> Dict:
    """
    获取数据库配置

    返回:
        database 配置字典
    """
    return get_config().get("database", {})


def get_database_pool_config() -> Dict:
    """
    获取数据库连接池配置

    返回:
        连接池配置字典，包含 min_size, max_size, timeout
    """
    db_config = get_database_config()
    pool_config = db_config.get("pool", {})
    return {
        "min_size": pool_config.get("min_size", 2),
        "max_size": pool_config.get("max_size", 20),
        "timeout": pool_config.get("timeout", 30)
    }


def get_ray_config() -> Dict:
    """
    获取 ray 配置

    返回:
        ray 配置字典
    """
    return get_config().get("ray", {})


def get_processor_manager_config() -> Dict:
    """
    获取 ProcessorManager 配置

    返回:
        processor_manager 配置字典
    """
    return get_config().get("processor_manager", {})


def get_sync_interval() -> int:
    """
    获取模型同步间隔

    返回:
        同步间隔（秒）
    """
    return get_processor_manager_config().get("sync_interval", 30)


def get_sync_max_retries() -> int:
    """
    获取同步失败最大重试次数

    返回:
        最大重试次数
    """
    return get_processor_manager_config().get("sync_max_retries", 3)


def get_sync_retry_delay() -> int:
    """
    获取同步重试初始延迟

    返回:
        初始延迟（秒）
    """
    return get_processor_manager_config().get("sync_retry_delay", 5)
