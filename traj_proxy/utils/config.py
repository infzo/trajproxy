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
    return Path(__file__).resolve().parents[2] / "configs" / config_name


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


def get_processor_manager_config() -> Dict:
    """
    获取 ProcessorManager 配置

    返回:
        processor_manager 配置字典
    """
    return get_config().get("processor_manager", {})


def get_sync_fallback_interval() -> int:
    """
    获取兜底全量同步间隔（秒）

    LISTEN/NOTIFY 为主通道，此为兜底机制。

    返回:
        兜底同步间隔（秒），默认 300
    """
    return get_processor_manager_config().get("sync_fallback_interval", 300)


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


def get_models_dir() -> str:
    """
    获取 models 目录路径

    优先级：
    1. config.yaml 中的 models_dir 配置
    2. 默认值 /app/models

    返回:
        models 目录的绝对路径
    """
    models_dir = get_config().get("models_dir", "/app/models")
    return os.path.abspath(models_dir)


def get_custom_parsers_dir() -> str:
    """
    获取自定义 parser 目录路径

    优先级：
    1. config.yaml 中的 custom_parsers_dir 配置
    2. 默认值 /app/custom_parsers

    返回:
        custom_parsers 目录的绝对路径
    """
    custom_parsers_dir = get_config().get("custom_parsers_dir", "/app/custom_parsers")
    return os.path.abspath(custom_parsers_dir)


def get_max_concurrent_requests() -> int:
    """
    获取单 worker 最大并发请求数

    返回:
        最大并发请求数，默认 128
    """
    return get_proxy_workers_config().get("max_concurrent_requests", 128)


def get_semaphore_acquire_timeout() -> float:
    """
    获取信号量获取超时秒数

    超过此时间未获取到信号量则返回 429。

    Returns:
        超时秒数，默认 5.0
    """
    return get_proxy_workers_config().get("semaphore_acquire_timeout", 5.0)


def get_gzip_config() -> Dict:
    """获取 HTTP gzip 响应压缩配置

    用于控制 ProxyWorker 是否对 HTTP 响应体启用 gzip 压缩。
    轨迹查询响应含大量 JSON 文本与整数数组，gzip 压缩率通常可达 5-10 倍，
    可显著降低传输数据量。默认关闭，需显式开启。

    配置项位于 config.yaml 的 proxy_workers 段：
    - gzip_enabled: 是否启用（默认 false）
    - gzip_minimum_size: 触发压缩的最小响应体字节数（默认 1024）

    Returns:
        {"enabled": bool, "minimum_size": int}
    """
    pw = get_proxy_workers_config()
    return {
        "enabled": pw.get("gzip_enabled", False),
        "minimum_size": pw.get("gzip_minimum_size", 1024),
    }


def get_processor_cache_max_size() -> int:
    """
    获取 LRU 缓存最大 Processor 数量

    Returns:
        最大 Processor 数，默认 32
    """
    return get_processor_manager_config().get("processor_cache_max_size", 32)


def get_processor_idle_timeout() -> int:
    """
    获取 Processor 空闲超时秒数

    超过此时间未被访问的 Processor 将被自动淘汰释放资源。
    设为 0 禁用空闲淘汰。

    Returns:
        空闲超时秒数，默认 300（5分钟）
    """
    return get_processor_manager_config().get("processor_idle_timeout", 300)


def get_infer_client_config() -> Dict:
    """
    获取 InferClient 配置

    返回:
        infer_client 配置字典，包含 connect_timeout, read_timeout, max_connections, max_retries
    """
    return get_config().get("infer_client", {
        "connect_timeout": 60,
        "read_timeout": 600,
        "max_connections": 1000,
        "max_retries": 2
    })


def get_storage_mode() -> str:
    """获取轨迹存储模式

    - "full": 全量模式，存储所有详情字段（默认）
    - "compact": 精简模式，跳过冗余字段存储（messages、text_request、text_response、
      token_ids、response_ids、token_request），这些字段可从其他字段导出

    Returns:
        存储模式字符串，默认 "full"
    """
    mode = get_database_config().get("storage_mode", "full")
    # 环境变量可覆盖
    env_mode = os.getenv("STORAGE_MODE")
    if env_mode:
        return env_mode
    return mode


def get_route_experts_offload_config() -> Dict:
    """获取 route_experts 大字段卸载配置

    配置段 route_experts_offload 包含卸载功能的总开关与后端参数。
    环境变量可覆盖部分字段（R3_OFFLOAD_ENABLED / R3_BACKEND / R3_TTL_HOURS 等）。

    注意：对 cfg 及子段（csb / local）均做浅拷贝，避免污染全局配置缓存。

    Returns:
        route_experts_offload 配置字典（独立副本，不影响全局缓存）
    """
    # 浅拷贝避免污染全局配置缓存；子段（csb/local）也需独立拷贝
    raw_cfg = get_config().get("route_experts_offload", {})
    cfg = dict(raw_cfg)  # 顶层浅拷贝

    # csb / local 子段独立拷贝，避免修改全局缓存的子 dict
    csb = dict(raw_cfg.get("csb", {}))
    local = dict(raw_cfg.get("local", {}))

    # 环境变量覆盖
    env_enabled = os.getenv("R3_OFFLOAD_ENABLED")
    if env_enabled is not None:
        cfg["enabled"] = env_enabled.lower() in ("true", "1", "yes")
    else:
        cfg.setdefault("enabled", False)

    env_backend = os.getenv("R3_BACKEND")
    if env_backend is not None:
        cfg["backend"] = env_backend

    env_ttl = os.getenv("R3_TTL_HOURS")
    if env_ttl is not None:
        cfg["ttl_hours"] = int(env_ttl)

    cfg.setdefault("ttl_hours", 2)
    cfg.setdefault("blob_key_prefix", "route_experts")
    cfg.setdefault("backend", "local")

    # csb 子段
    env_csb_token = os.getenv("CSB_APP_TOKEN")
    if env_csb_token is not None:
        csb["app_token"] = env_csb_token
    csb.setdefault("app_token", "")
    csb.setdefault("bucket", "trajproxy-r3")
    csb.setdefault("endpoint", "https://csb.example.com")
    csb.setdefault("verify_tls", True)
    cfg["csb"] = csb  # 把拷贝后的 csb 赋回 cfg

    # local 子段
    env_write_path = os.getenv("R3_LOCAL_WRITE_PATH")
    if env_write_path is not None:
        local["write_path"] = env_write_path
    env_access_path = os.getenv("R3_LOCAL_ACCESS_PATH")
    if env_access_path is not None:
        local["access_path"] = env_access_path
    local.setdefault("write_path", "/data/r3")
    local.setdefault("access_path", local["write_path"])  # 留空默认 = write_path
    cfg["local"] = local  # 把拷贝后的 local 赋回 cfg

    return cfg
