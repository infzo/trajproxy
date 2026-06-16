"""
基数防护 — 防止高 cardinality 导致 Prometheus 内存爆炸

职责：
- 已知 model 白名单：避免每个请求查询 ProcessorManager
- run_id LRU 封顶（30）：最久未使用的 run_id 被淘汰并触发清理钩子
- 淘汰时调用已注册的清理钩子，允许下游（如 metrics_collector）移除对应的 Prometheus 子指标
- 未注册模型归为 "unknown"
"""

from collections import OrderedDict
from typing import Set

from traj_proxy.observability.event_bus import subscribe
from traj_proxy.observability.events import EVENT_MODEL_LIFECYCLE

# 已知 model 集合（进程级）
_KNOWN_MODELS: Set[str] = set()
# run_id 使用 OrderedDict 实现 LRU 淘汰（最近使用的在尾部，最久未使用的在头部）
_KNOWN_RUN_IDS: OrderedDict[str, None] = OrderedDict()
_MAX_RUN_ID_CARDINALITY = 30

# 清理钩子：当 run_id 被淘汰或注销时，通知下游清理对应的 Prometheus 子指标
_CLEANUP_HOOKS: list = []


def register_cleanup_hook(fn) -> None:
    """注册清理钩子，当 run_id 被淘汰或注销时调用 fn(evicted_run_id)"""
    _CLEANUP_HOOKS.append(fn)


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
    """注册 run_id（run_id 白名单添加），显式生命周期 API"""
    _KNOWN_RUN_IDS[run_id] = None


def unregister_known_run_id(run_id: str) -> None:
    """注销 run_id，并调用清理钩子通知下游移除对应的子指标"""
    if run_id in _KNOWN_RUN_IDS:
        _KNOWN_RUN_IDS.pop(run_id)
        for hook in _CLEANUP_HOOKS:
            hook(run_id)


def safe_run_id_label(run_id: str) -> str:
    """run_id = 作业/租户级标识，基数封顶 30，采用 LRU 淘汰策略
    
    - 已存在的 run_id：移动到尾部（标记为最近使用），返回原值
    - 容量已满且为新 run_id：淘汰最久未使用的（头部），腾出位置添加新 run_id，返回原值
    - 容量未满且为新 run_id：添加到尾部，返回原值
    """
    if not run_id:
        return ""
    if run_id in _KNOWN_RUN_IDS:
        # 已存在，移动到尾部标记为最近使用
        _KNOWN_RUN_IDS.move_to_end(run_id)
        return run_id
    if len(_KNOWN_RUN_IDS) >= _MAX_RUN_ID_CARDINALITY:
        # 容量已满，淘汰最久未使用的（头部），腾出位置给新的 run_id
        evicted_run_id, _ = _KNOWN_RUN_IDS.popitem(last=False)
        for hook in _CLEANUP_HOOKS:
            hook(evicted_run_id)
    # 添加新 run_id 到尾部
    _KNOWN_RUN_IDS[run_id] = None
    return run_id