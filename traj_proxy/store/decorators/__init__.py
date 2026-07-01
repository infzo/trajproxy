"""
Store Decorators 子包 - 透明增强 Repository 的 Decorator 组件

功能边界:
    存放包裹 Repository 的 Decorator。其特点:
    - 对外实现 Repository 接口（duck-type 透明替换，对下游 Pipeline/Processor 无感）
    - 不持有 AsyncConnectionPool，不直接执行 raw SQL
    - 通过委托 inner Repository + 其他 Repository 间接访问 DB

    与 store/ 根目录的真 Repository（注入 pool + raw SQL）物理隔离，
    便于区分职责，避免按真 Repository 约定去预期 Decorator。
    详见 store/CLAUDE.md 第二节 B 类。

对外接口:
    - OffloadingRepository: route_experts 大字段卸载 Decorator

依赖关系:
    - traj_proxy.store.request_repository.RequestRepository (inner)
    - traj_proxy.store.r3_ref_repository.R3RefRepository
    - traj_proxy.store.blob_storage.BlobStorage
"""

from traj_proxy.store.decorators.offloading_repository import OffloadingRepository

__all__ = ["OffloadingRepository"]
