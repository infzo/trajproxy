"""
归档进程独立配置加载

不依赖 traj_proxy 的任何模块。
"""

import os
from pathlib import Path
from typing import Dict, Optional

import yaml


_config: Optional[Dict] = None


def _get_config_path() -> Path:
    """获取配置文件路径

    优先级：
    1. 环境变量 ARCHIVER_CONFIG
    2. 项目根目录 configs/archiver.yaml
    """
    env_path = os.getenv("ARCHIVER_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()

    # 从 traj_archiver/ 向上两级到项目根目录
    return Path(__file__).resolve().parents[1] / "configs" / "archiver.yaml"


def load_config() -> Dict:
    """加载配置文件"""
    global _config
    if _config is None:
        config_path = _get_config_path()
        with open(config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def get_config() -> Dict:
    """获取已加载的配置"""
    if _config is None:
        return load_config()
    return _config


def get_database_url() -> str:
    """获取数据库连接 URL

    优先级：
    1. 环境变量 DATABASE_URL
    2. 配置文件 database.url
    """
    return os.getenv("DATABASE_URL") or get_config().get("database", {}).get("url", "")


def get_archive_config() -> Dict:
    """获取归档配置

    返回完整的 archive 配置，storage 模块根据 s3 字段自动选择后端。
    """
    return get_config().get("archive", {
        "retention_days": 30,
        "poll_interval": 3600,
        "storage_path": "/data/archives",
        "local_temp_path": "/tmp/archives",
    })


def get_database_pool_config() -> Dict:
    """获取数据库连接池配置"""
    db_config = get_config().get("database", {})
    pool_config = db_config.get("pool", {})
    return {
        "min_size": pool_config.get("min_size", 2),
        "max_size": pool_config.get("max_size", 10),
        "timeout": pool_config.get("timeout", 30),
    }
