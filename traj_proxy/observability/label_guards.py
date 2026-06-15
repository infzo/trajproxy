"""
基数防护 — 防止高 cardinality 导致 Prometheus 内存爆炸

职责：
- 已知 model 白名单：避免每个请求查询 ProcessorManager
- run_id 封顶（100）：超出归 "other"
- 未注册模型归为 "unknown"
"""

from typing import Set

from traj_proxy.observability.event_bus import subscribe
from traj_proxy.observability.events import EVENT_MODEL_LIFECYCLE

# 已知 model 和 run_id 集合（进程级）
_KNOWN_MODELS: Set[str] = set()
_KNOWN_RUN_IDS: Set[str] = set()
_MAX_RUN_ID_CARDINALITY = 100


def init_known_models() -> None:
    """启动时从 ProcessorManager 加载已知模型（懒加载，首次请求前执行）"""
    # 通过 EVENT_MODEL_LIFECYCLE 事件增量更新
    _register_model_lifecycle_handler()


def _register_model_lifecycle_handler() -> None:
    """订阅模型生命周期事件更新缓存"""

    def handler(action: str, model: str, **_kwargs) -> None:
        if action == "register":
            _KNOWN_MODELS.add(model)
        elif action == "unregister":
            _KNOWN_MODELS.discard(model)

    subscribe(EVENT_MODEL_LIFECYCLE, handler)


def refresh_known_models(models: Set[str]) -> None:
    """由外部触发全量刷新"""
    global _KNOWN_MODELS
    _KNOWN_MODELS = set(models)


def safe_model_label(model: str) -> str:
    """未注册模型归为 'unknown'，防止恶意 model 名制造序列"""
    return model if model in _KNOWN_MODELS else "unknown"


def register_known_run_id(run_id: str) -> None:
    """注册 run_id（run_id 白名单添加）"""
    _KNOWN_RUN_IDS.add(run_id)


def unregister_known_run_id(run_id: str) -> None:
    """注销 run_id"""
    _KNOWN_RUN_IDS.discard(run_id)


def safe_run_id_label(run_id: str) -> str:
    """run_id = 作业/租户级标识，基数封顶 100"""
    if not run_id:
        return ""
    if len(_KNOWN_RUN_IDS) >= _MAX_RUN_ID_CARDINALITY and run_id not in _KNOWN_RUN_IDS:
        return "other"
    _KNOWN_RUN_IDS.add(run_id)
    return run_id