"""
核心归档逻辑 - 按 run_id 粒度将过期数据归档到存储后端

协调器模式：
  1. 查找全部记录已过期的 run_id (MAX(created_at) < threshold)
  2. 逐 run 查询 session 列表
  3. 将 session 分发给 Ray Worker 并发执行（读 DB → 写文件 → 上传）
  4. 全部 session 完成后执行 DELETE + UPDATE
  5. 清理空的月分区
"""

import asyncio
import logging
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ray
from ray.exceptions import GetTimeoutError, RayActorError

from traj_archiver.config import get_archive_config, get_database_url
from traj_archiver.session_worker import SessionArchiveWorker

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_path(segment: str) -> str:
    return segment.replace(",", "_").replace("/", "_")


# ============================================================
# 分区管理
# ============================================================


async def ensure_current_partition(conn):
    """确保当月和下月分区存在"""
    now = _utcnow()
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = datetime(next_year, next_month, 1, tzinfo=timezone.utc)
    next_next_month_start = datetime(
        next_year + (1 if next_month == 12 else 0),
        next_month + 1 if next_month < 12 else 1,
        1, tzinfo=timezone.utc,
    )

    partition_name = f"request_details_active_{now.strftime('%Y_%m')}"
    next_partition_name = f"request_details_active_{next_year:04d}_{next_month:02d}"

    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relnamespace = 'public'::regnamespace",
            (partition_name,),
        )
        if not await cur.fetchone():
            await _create_partition(conn, partition_name, month_start, next_month_start)

    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relnamespace = 'public'::regnamespace",
            (next_partition_name,),
        )
        if not await cur.fetchone():
            await _create_partition(conn, next_partition_name, next_month_start, next_next_month_start)

    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT 1 FROM pg_class
            WHERE relname = 'request_details_active_default'
              AND relnamespace = 'public'::regnamespace
        """)
        if not await cur.fetchone():
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS public.request_details_active_default
                    PARTITION OF public.request_details_active DEFAULT
            """)
            logger.info("已创建默认分区: request_details_active_default")


async def _create_partition(conn, name, start, end):
    """创建月分区，处理默认分区数据冲突"""
    try:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS public.{name}
                PARTITION OF public.request_details_active
                FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')
        """)
        logger.info(f"已创建分区: {name}")
    except Exception as e:
        if "default partition" not in str(e) or "would be violated" not in str(e):
            raise

        logger.info(f"默认分区存在冲突数据，迁移到 {name}...")
        await conn.execute("""
            ALTER TABLE public.request_details_active
            DETACH PARTITION public.request_details_active_default
        """)
        await conn.execute(f"""
            CREATE TABLE public.{name}
                PARTITION OF public.request_details_active
                FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')
        """)
        await conn.execute(f"""
            INSERT INTO public.{name}
                SELECT * FROM public.request_details_active_default
                WHERE created_at >= '{start.isoformat()}'
                  AND created_at < '{end.isoformat()}'
        """)
        await conn.execute(f"""
            DELETE FROM public.request_details_active_default
            WHERE created_at >= '{start.isoformat()}'
              AND created_at < '{end.isoformat()}'
        """)
        await conn.execute("""
            ALTER TABLE public.request_details_active
            ATTACH PARTITION public.request_details_active_default DEFAULT
        """)
        logger.info(f"数据迁移完成: {name}")


async def _find_partitions(conn) -> List[str]:
    """列出所有子分区名"""
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT pp.relname
            FROM pg_class pt
            JOIN pg_inherits i ON pt.oid = i.inhparent
            JOIN pg_class pp ON i.inhrelid = pp.oid
            WHERE pt.relname = 'request_details_active'
              AND pt.relnamespace = 'public'::regnamespace
            ORDER BY pp.relname
        """)
        return [r[0] for r in await cur.fetchall()]


async def _is_partition_empty(conn, partition_name: str) -> bool:
    """检查分区是否为空"""
    async with conn.cursor() as cur:
        await cur.execute(f"SELECT 1 FROM public.{partition_name} LIMIT 1")
        return await cur.fetchone() is None


# ============================================================
# Worker 健康检查与重建
# ============================================================


async def _check_worker_health(
    workers: List,
    worker_factory_params: Dict[str, Any],
) -> List:
    """检查所有 Worker 的存活状态，移除死亡 Worker 并重建替代

    Args:
        workers: 当前 Worker Actor 列表
        worker_factory_params: 创建新 Worker 所需的参数字典，包含：
            db_url, storage_config, temp_root, compress

    Returns:
        更新后的 Worker 列表（死亡 Worker 已替换为新 Worker）
    """
    healthy = []
    rebuilt_count = 0
    for i, worker in enumerate(workers):
        try:
            # 简单 ping：调用一个极轻量的方法确认 Actor 存活
            await asyncio.to_thread(ray.get, worker.ping.remote(), timeout=5)
            healthy.append(worker)
        except (RayActorError, ray.exceptions.RayTaskError, GetTimeoutError) as e:
            # Ray 系列错误（死亡/任务异常/超时）：Worker Actor 不存活，需要重建
            logger.warning(
                f"Worker-{i} 不存活（{type(e).__name__}: {e}），正在重建..."
            )
            # 释放旧 Worker 的 Ray Actor 资源，避免孤儿 Actor 占用 CPU 配额
            try:
                ray.kill(worker, no_restart=True)
                logger.debug(f"Worker-{i} 旧 Actor 已 kill 释放资源")
            except Exception as kill_err:
                # Actor 可能已死亡或无响应，kill 失败可忽略
                logger.debug(f"Kill Worker-{i} 失败（可忽略）: {kill_err}")
            new_worker = SessionArchiveWorker.remote(
                worker_id=i,
                db_url=worker_factory_params["db_url"],
                storage_config=worker_factory_params["storage_config"],
                temp_root=worker_factory_params["temp_root"],
                compress=worker_factory_params["compress"],
            )
            healthy.append(new_worker)
            rebuilt_count += 1
            logger.info(f"Worker-{i} 已重建为新 Actor")
        except Exception as e:
            # 非 Ray 错误（网络超时等），无法确认存活但不确定已死亡
            # 保守策略：保留原 Worker，不重建，记录为异常
            logger.error(
                f"Worker-{i} 健康检查未知错误（{type(e).__name__}: {e}），"
                f"保留原 Worker 不重建"
            )
            healthy.append(worker)

    if rebuilt_count > 0:
        logger.warning(
            f"Worker 健康检查: 重建了 {rebuilt_count}/{len(workers)} 个死亡 Worker"
        )
    return healthy


def _rebuild_worker_in_list(
    workers: List,
    dead_worker,
    worker_factory_params: Dict[str, Any],
) -> Tuple[List, Any]:
    """在可用 Worker 列表中重建死亡 Worker，返回 (新列表, 新 Worker)

    Args:
        workers: 当前可用 Worker 列表
        dead_worker: 已死亡的 Worker Actor handle
        worker_factory_params: 创建新 Worker 所需的参数

    Returns:
        (更新后的 Worker 列表, 新创建的 Worker Actor)
    """
    try:
        dead_idx = workers.index(dead_worker)
    except ValueError:
        dead_idx = len(workers)

    # 释放旧 Worker 的 Ray Actor 资源，避免孤儿 Actor 占用 CPU 配额
    try:
        ray.kill(dead_worker, no_restart=True)
        logger.debug(f"Worker-{dead_idx} 旧 Actor 已 kill 释放资源")
    except Exception as kill_err:
        # Actor 可能已死亡或无响应，kill 失败可忽略
        logger.debug(f"Kill Worker-{dead_idx} 失败（可忽略）: {kill_err}")

    new_worker = SessionArchiveWorker.remote(
        worker_id=dead_idx,
        db_url=worker_factory_params["db_url"],
        storage_config=worker_factory_params["storage_config"],
        temp_root=worker_factory_params["temp_root"],
        compress=worker_factory_params["compress"],
    )

    logger.warning(f"Worker-{dead_idx} 已死亡，重建新 Actor 替换")

    new_workers = list(workers)
    new_workers[dead_idx] = new_worker
    return new_workers, new_worker


# ============================================================
# 归档判定：按 run_id 粒度
# ============================================================


async def _find_expired_runs(conn, threshold: datetime) -> List[str]:
    """找到所有记录都已过期的 run_id（非 NULL）"""
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT m.run_id
            FROM request_details_active d
            JOIN request_metadata m ON d.unique_id = m.unique_id
            WHERE m.run_id IS NOT NULL AND m.run_id != ''
            GROUP BY m.run_id
            HAVING MAX(d.created_at) < %s
            ORDER BY m.run_id
        """, (threshold,))
        return [r[0] for r in await cur.fetchall()]


async def _get_run_sessions(conn, run_id: str) -> List[Optional[str]]:
    """查询 run 下的 session 列表（包含 NULL）"""
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT DISTINCT m.session_id
            FROM request_metadata m
            JOIN request_details_active d ON d.unique_id = m.unique_id
            WHERE m.run_id = %s
        """, (run_id,))
        return [r[0] for r in await cur.fetchall()]


# ============================================================
# 归档执行
# ============================================================


async def archive_details(
    pool,
    workers,
    storage,
    local_temp_path: str,
    retention_days: int,
) -> Tuple[List, Dict[str, Any]]:
    """按 run_id 粒度归档过期数据

    Args:
        pool: 协理器 DB 连接池（仅用于查询和 cleanup）
        workers: Ray Actor Worker 列表
        storage: 存储后端（协调器用，获取 location_prefix）
        local_temp_path: Worker 临时目录
        retention_days: 保留天数

    Returns:
        (更新后的 workers 列表, 归档结果字典)
        workers 列表可能因重建死亡 Worker 而变化，调用方应保存以供下次使用。
    """
    threshold = _utcnow() - timedelta(days=retention_days)
    temp_root = Path(local_temp_path)
    temp_root.mkdir(parents=True, exist_ok=True)

    result = {
        "runs_processed": 0,
        "sessions_archived": 0,
        "records_archived": 0,
        "partitions_cleaned": 0,
        "archive_files": [],
        "errors": [],
        "details": [],
        "worker_rebuilds": 0,
    }

    # 从配置推断 worker_factory_params（用于重建死亡 Worker）
    archive_config = get_archive_config()
    worker_factory_params = {
        "db_url": get_database_url(),
        "storage_config": archive_config,
        "temp_root": local_temp_path,
        "compress": archive_config.get("compress", True),
    }

    # 阶段 0: 健康检查 — 确保所有 Worker 存活再开始
    try:
        workers = await _check_worker_health(workers, worker_factory_params)
    except Exception as e:
        logger.warning(f"Worker 健康检查失败（将使用当前列表继续）: {e}")

    # 阶段 1: 查找过期 run（短连接，立即释放）
    async with pool.connection() as conn:
        await ensure_current_partition(conn)
        expired_runs = await _find_expired_runs(conn, threshold)
        await conn.commit()

    logger.info(
        f"阈值: {threshold.isoformat()}, "
        f"找到 {len(expired_runs)} 个过期 run, "
        f"可用 Worker: {len(workers)}"
    )

    # 阶段 2: 每个 run 使用独立连接，互不影响
    total = len(expired_runs)
    worker_rebuilds_total = 0
    for idx, run_id in enumerate(expired_runs, 1):
        logger.info(f"[{idx}/{total}] 开始归档 run '{run_id}'...")
        try:
            workers, stats = await _archive_run(
                pool, workers, storage, run_id, worker_factory_params,
            )
            result["runs_processed"] += 1
            result["sessions_archived"] += stats["sessions"]
            result["records_archived"] += stats["records"]
            result["archive_files"].extend(stats["files"])
            result["details"].append({
                "run_id": run_id,
                "sessions": stats["sessions"],
                "records": stats["records"],
                "failed_sessions": stats.get("failed_sessions", 0),
            })
        except Exception as e:
            msg = f"归档 run '{run_id}' 失败: {e}"
            logger.error(msg)
            logger.debug(traceback.format_exc())
            result["errors"].append(msg)

    # 阶段 3: 清理空分区（独立连接）
    if result["runs_processed"] > 0:
        try:
            async with pool.connection() as conn:
                cleaned = await _cleanup_empty_partitions(conn)
                result["partitions_cleaned"] = cleaned
        except Exception as e:
            logger.warning(f"清理空分区失败: {e}")

    return workers, result


async def _archive_run(
    pool,
    workers,
    storage,
    run_id: str,
    worker_factory_params: Dict[str, Any],
) -> Tuple[List, Dict[str, Any]]:
    """归档一个 run：查询 session 列表 → 分发给 Workers → cleanup

    Args:
        pool: DB 连接池
        workers: 当前可用的 Worker 列表
        storage: 存储后端
        run_id: 待归档的 run ID
        worker_factory_params: 创建新 Worker 所需的参数

    Returns:
        (更新后的 workers 列表, 归档统计字典)
        workers 列表可能因重建死亡 Worker 而变化。
    """
    safe_run = _safe_path(run_id)
    t_start = time.monotonic()
    worker_rebuilds = 0

    # 1. 查询 session 列表（协调器连接，短用即还）
    async with pool.connection() as conn:
        sessions = await _get_run_sessions(conn, run_id)

    if not sessions:
        logger.info(f"  run '{run_id}' 无活跃记录，跳过")
        return workers, {"sessions": 0, "records": 0, "files": [], "failed_sessions": 0}

    logger.info(f"  run '{run_id}': {len(sessions)} 个 session 待归档")

    # 2. 动态分发：Worker 完成一个 session 后再领下一个，避免大 session 排队
    total_sessions = len(sessions)
    pending = list(enumerate(sessions))  # [(idx, session_id), ...]
    active: Dict = {}  # future -> (worker, session_id, idx)
    raw_results = []
    failed_sessions = []  # 因业务错误或超出重试上限而失败的 session
    successful_session_ids: List[Optional[str]] = []  # 成功归档的 session_id 列表，用于精确清理

    # 可用 Worker 列表（可能因重建而变化）
    available_workers = list(workers)

    # ---- 连续重建计数器：防止所有 Worker 集体崩溃时的死循环 ----
    # 允许每个 Worker 最多重建 3 轮，总上限 = Worker 数量 × 3
    # 设计理由：正常场景下 Worker 崩溃是偶发的，3 次/Worker 足以覆盖瞬时故障；
    # 当共享资源（DB）故障导致集体崩溃时，计数器会快速达到上限并中止，
    # 避免无限创建 Actor 耗尽 Ray CPU 配额。
    max_consecutive_rebuilds = len(available_workers) * 3
    consecutive_rebuilds = 0

    # ---- 会话级别失败计数器：防止畸形数据导致单个 session 无限重试 ----
    # 同一 session 连续失败 3 次后，从 pending 中移除并标记为 failure。
    # 设计理由：业务错误已在 except Exception 中不重试，但 Worker 崩溃导致的
    # RayActorError 会放回 pending；如果某个 session 的数据畸形导致 Worker
    # 每次处理它都崩溃，3 次上限足以确认该 session 有问题。
    MAX_SESSION_RETRIES = 3
    session_fail_counts: Dict[str, int] = {}  # session_id -> 连续失败次数

    # 初始填满：每个 Worker 先领一个
    for worker in available_workers:
        if not pending:
            break
        idx, session_id = pending.pop(0)
        try:
            f = worker.archive.remote(run_id, session_id, idx + 1, total_sessions)
            active[f] = (worker, session_id, idx)
        except RayActorError:
            # Worker 在提交任务时已死亡，重建并重新提交
            consecutive_rebuilds += 1
            if consecutive_rebuilds >= max_consecutive_rebuilds:
                logger.error(
                    f"Worker 连续重建达到上限 {max_consecutive_rebuilds}，中止归档"
                )
                # 当前 session 也放入 pending 一并处理
                pending.append((idx, session_id))
                for p_idx, sid in pending:
                    session_fail_counts.setdefault(sid, 0)
                    session_fail_counts[sid] += 1
                    failed_sessions.append({
                        "session_id": sid,
                        "idx": p_idx,
                        "error": f"Worker 连续重建达到上限 {max_consecutive_rebuilds}，session 未处理",
                    })
                pending.clear()
                break
            logger.warning(f"  Worker 提交任务时已死亡，正在重建...")
            available_workers, worker = _rebuild_worker_in_list(
                available_workers, worker, worker_factory_params,
            )
            worker_rebuilds += 1
            # 将 session 放回 pending 尾部，稍后重试
            pending.append((idx, session_id))

    # 循环等待：完成一个就补一个
    # 条件增加 consecutive_rebuilds < max 限制，防止所有 Worker 集体崩溃时死循环
    while active or (pending and available_workers and consecutive_rebuilds < max_consecutive_rebuilds):
        # 如果 active 为空但 pending 不为空，需要分配新任务
        if not active and pending and available_workers:
            for worker in available_workers:
                if not pending:
                    break
                idx, session_id = pending.pop(0)
                try:
                    f = worker.archive.remote(
                        run_id, session_id, idx + 1, total_sessions,
                    )
                    active[f] = (worker, session_id, idx)
                except RayActorError:
                    consecutive_rebuilds += 1
                    if consecutive_rebuilds >= max_consecutive_rebuilds:
                        logger.error(
                            f"Worker 连续重建达到上限 {max_consecutive_rebuilds}，中止归档"
                        )
                        pending.append((idx, session_id))
                        break
                    logger.warning(f"  Worker 提交任务时已死亡，正在重建...")
                    available_workers, worker = _rebuild_worker_in_list(
                        available_workers, worker, worker_factory_params,
                    )
                    worker_rebuilds += 1
                    # 会话级别失败计数
                    session_fail_counts.setdefault(session_id, 0)
                    session_fail_counts[session_id] += 1
                    pending.append((idx, session_id))
            # 内层循环也需要检查连续重建上限
            if consecutive_rebuilds >= max_consecutive_rebuilds:
                break
            # 过滤掉超出重试上限的 session
            pending = [
                (i, sid) for i, sid in pending
                if session_fail_counts.get(sid, 0) < MAX_SESSION_RETRIES
            ]
            # 超出上限的 session 记入 failed_sessions
            exceeded = [
                sid for sid, cnt in session_fail_counts.items()
                if cnt >= MAX_SESSION_RETRIES and sid not in
                {fs["session_id"] for fs in failed_sessions}
            ]
            for sid in exceeded:
                failed_sessions.append({
                    "session_id": sid,
                    "idx": -1,  # idx 已丢失，标记为 -1
                    "error": f"session 连续失败 {MAX_SESSION_RETRIES} 次，超出重试上限",
                })
            continue

        if not active:
            break

        done_futures, _ = await asyncio.to_thread(
            ray.wait, list(active.keys()), num_returns=1, timeout=120,
        )
        if not done_futures:
            logger.warning(
                f"  run '{run_id}': 2 分钟内无 Worker 完成，"
                f"剩余 {len(active)} 个 session 处理中..."
            )
            continue

        for f in done_futures:
            worker, session_id, idx = active.pop(f)
            try:
                result = await asyncio.to_thread(ray.get, f, timeout=10)
                raw_results.append(result)
                successful_session_ids.append(session_id)
            except (RayActorError, GetTimeoutError) as e:
                # Worker 进程已死亡或结果获取超时 — 不取消其他 Worker 的任务
                consecutive_rebuilds += 1
                # 会话级别失败计数
                session_fail_counts.setdefault(session_id, 0)
                session_fail_counts[session_id] += 1

                if consecutive_rebuilds >= max_consecutive_rebuilds:
                    logger.error(
                        f"Worker 连续重建达到上限 {max_consecutive_rebuilds}，"
                        f"session [{idx + 1}/{total_sessions}] 及后续 session 将不再重试"
                    )
                    # 将当前 session 记入 failed
                    failed_sessions.append({
                        "session_id": session_id,
                        "idx": idx,
                        "error": f"Worker 连续重建达到上限 {max_consecutive_rebuilds}",
                    })
                    # 取消同 Worker 的其他任务也记入 failed
                    for other_f, (other_w, other_sid, other_idx) in list(active.items()):
                        if other_w is worker:
                            active.pop(other_f)
                            ray.cancel(other_f, force=True)
                            failed_sessions.append({
                                "session_id": other_sid,
                                "idx": other_idx,
                                "error": f"Worker 连续重建达到上限 {max_consecutive_rebuilds}",
                            })
                    # 不放回 pending，直接跳出 for 循环
                    continue

                logger.error(
                    f"  session [{idx + 1}/{total_sessions}] 失败: "
                    f"Worker 死亡（RayActorError），将重建 Worker 并重试该 session"
                )
                # 重建死亡 Worker
                available_workers, new_worker = _rebuild_worker_in_list(
                    available_workers, worker, worker_factory_params,
                )
                worker_rebuilds += 1
                # 将失败的 session 放回 pending 重试（仅在会话重试上限内）
                if session_fail_counts[session_id] < MAX_SESSION_RETRIES:
                    pending.append((idx, session_id))
                else:
                    failed_sessions.append({
                        "session_id": session_id,
                        "idx": idx,
                        "error": f"session 连续失败 {MAX_SESSION_RETRIES} 次，超出重试上限",
                    })
                # 同时检查该 Worker 是否有其他正在进行的任务
                for other_f, (other_w, other_sid, other_idx) in list(active.items()):
                    if other_w is worker:
                        active.pop(other_f)
                        ray.cancel(other_f, force=True)
                        session_fail_counts.setdefault(other_sid, 0)
                        session_fail_counts[other_sid] += 1
                        if session_fail_counts[other_sid] < MAX_SESSION_RETRIES:
                            pending.append((other_idx, other_sid))
                        else:
                            failed_sessions.append({
                                "session_id": other_sid,
                                "idx": other_idx,
                                "error": f"session 连续失败 {MAX_SESSION_RETRIES} 次，超出重试上限",
                            })
                        logger.warning(
                            f"  同 Worker 的 session [{other_idx + 1}/{total_sessions}] "
                            f"也已取消，将重试"
                        )

            except Exception as e:
                # 普通业务异常（DB 错误、上传失败等）— 只记录，不中止整个 run
                logger.error(
                    f"  session [{idx + 1}/{total_sessions}] 失败: {e}"
                )
                # 将失败 session 放入 failed_sessions，不重试（业务错误重试通常也会失败）
                failed_sessions.append({
                    "session_id": session_id,
                    "idx": idx,
                    "error": str(e),
                })

            # 该 Worker 空闲，分配下一个 session（仅 Worker 存活时）
            # 成功完成一个 session 时，连续重建计数器归零
            else:
                consecutive_rebuilds = 0
                if pending:
                    next_idx, next_session = pending.pop(0)
                    try:
                        nf = worker.archive.remote(
                            run_id, next_session, next_idx + 1, total_sessions,
                        )
                        active[nf] = (worker, next_session, next_idx)
                    except RayActorError:
                        consecutive_rebuilds += 1
                        if consecutive_rebuilds >= max_consecutive_rebuilds:
                            logger.error(
                                f"Worker 连续重建达到上限 {max_consecutive_rebuilds}，中止归档"
                            )
                            failed_sessions.append({
                                "session_id": next_session,
                                "idx": next_idx,
                                "error": f"Worker 连续重建达到上限 {max_consecutive_rebuilds}",
                            })
                        else:
                            logger.warning(f"  Worker 分配新任务时已死亡，正在重建...")
                            available_workers, worker = _rebuild_worker_in_list(
                                available_workers, worker, worker_factory_params,
                            )
                            worker_rebuilds += 1
                            # 会话级别失败计数
                            session_fail_counts.setdefault(next_session, 0)
                            session_fail_counts[next_session] += 1
                            if session_fail_counts[next_session] < MAX_SESSION_RETRIES:
                                pending.append((next_idx, next_session))
                            else:
                                failed_sessions.append({
                                    "session_id": next_session,
                                    "idx": next_idx,
                                    "error": f"session 连续失败 {MAX_SESSION_RETRIES} 次，超出重试上限",
                                })

    # 更新 workers 列表（返回给调用者，后续 run 可使用新列表）
    workers = available_workers

    # ---- 循环结束后的清理逻辑 ----

    # 1. 如果连续重建达到上限：将剩余 pending session 全部标记为 failure
    if consecutive_rebuilds >= max_consecutive_rebuilds and pending:
        logger.error(
            f"  run '{run_id}': Worker 连续重建达到上限，"
            f"剩余 {len(pending)} 个 session 标记为失败"
        )
        for _, sid in pending:
            # 避免重复记录（可能已在循环内标记过）
            if sid not in {fs["session_id"] for fs in failed_sessions}:
                failed_sessions.append({
                    "session_id": sid,
                    "idx": -1,
                    "error": f"Worker 连续重建达到上限 {max_consecutive_rebuilds}，session 未处理",
                })
        pending.clear()

    # 2. 如果 pending 不为空但 available_workers 为空（Worker 已全部死亡且无法重建）
    #    这种情况理论上不应出现（重建是替换而非移除），但作为防御性检查
    if pending and not available_workers:
        logger.error(
            f"  run '{run_id}': Worker 列表为空，"
            f"剩余 {len(pending)} 个 session 标记为失败"
        )
        for _, sid in pending:
            if sid not in {fs["session_id"] for fs in failed_sessions}:
                failed_sessions.append({
                    "session_id": sid,
                    "idx": -1,
                    "error": "所有 Worker 已死亡且列表为空，session 未处理",
                })
        pending.clear()

    if failed_sessions:
        logger.warning(
            f"  run '{run_id}': {len(failed_sessions)} 个 session 因错误跳过"
        )

    if worker_rebuilds > 0:
        logger.info(
            f"  run '{run_id}': 重建了 {worker_rebuilds} 次 Worker"
        )

    # 如果没有任何成功结果且有 failed_sessions，说明所有 Worker 都死亡
    if not raw_results and failed_sessions:
        raise RuntimeError(
            f"run '{run_id}' 归档失败: 所有 Worker 均不可用，"
            f"{len(failed_sessions)} 个 session 未处理"
        )

    t_workers = time.monotonic()
    logger.info(
        f"  Workers 完成: {total_sessions} 个 session, "
        f"耗时 {t_workers - t_start:.1f}s"
    )

    # 3. 收集统计
    total_records = 0
    files = []
    for r in raw_results:
        total_records += r["records"]
        if r["file"] is not None:
            files.append(r["file"])

    # 4. 仅清理成功归档的 session（避免部分失败时数据丢失）
    archive_location = f"{storage.location_prefix}{safe_run}/"
    async with pool.connection() as conn:
        await _cleanup_run(conn, run_id, archive_location, successful_session_ids)

    t_done = time.monotonic()
    logger.info(
        f"  归档 run '{run_id}' 完成: {len(files)} 个文件, {total_records} 条记录, "
        f"总耗时 {t_done - t_start:.1f}s "
        f"(Workers {t_workers - t_start:.1f}s / 清理 {t_done - t_workers:.1f}s)"
    )
    return workers, {
        "sessions": len(files),
        "records": total_records,
        "files": files,
        "failed_sessions": len(failed_sessions),
    }


async def _cleanup_run(
    conn,
    run_id: str,
    archive_location: str,
    successful_session_ids: List[Optional[str]],
) -> None:
    """按成功归档的 session 列表清理：DELETE details + UPDATE metadata

    仅清理 successful_session_ids 中记录的成功 session，
    失败 session 的记录保留，等待下次归档周期重试。

    Args:
        conn: 数据库连接
        run_id: 待归档的 run ID
        archive_location: 归档位置前缀
        successful_session_ids: 成功归档的 session_id 列表（含 None 表示 NULL session_id）
    """
    if not successful_session_ids:
        logger.warning(f"run_id={run_id}: 无成功 archive 的 session，跳过清理")
        return

    # 构建 session_id 过滤条件，处理 NULL session_id 情况
    has_null = None in successful_session_ids
    non_null_ids = [sid for sid in successful_session_ids if sid is not None]

    if has_null and non_null_ids:
        placeholders = ",".join(["%s"] * len(non_null_ids))
        session_filter = f"(session_id IS NULL OR session_id IN ({placeholders}))"
        subquery_params: Tuple = (run_id, *non_null_ids)
    elif has_null:
        session_filter = "session_id IS NULL"
        subquery_params = (run_id,)
    else:
        placeholders = ",".join(["%s"] * len(non_null_ids))
        session_filter = f"session_id IN ({placeholders})"
        subquery_params = (run_id, *non_null_ids)

    async with conn.transaction():
        # 1. DELETE 成功归档 session 的 details
        await conn.execute(
            f"DELETE FROM request_details_active "
            f"WHERE unique_id IN ("
            f"  SELECT unique_id FROM request_metadata "
            f"  WHERE run_id = %s AND {session_filter}"
            f")",
            subquery_params,
        )
        # 2. UPDATE 成功归档 session 的 metadata（标记 archive_location）
        await conn.execute(
            f"UPDATE request_metadata "
            f"SET archive_location = %s, archived_at = NOW() "
            f"WHERE run_id = %s AND {session_filter} AND archive_location IS NULL",
            (archive_location, *subquery_params),
        )

    logger.info(
        f"  run '{run_id}': {len(successful_session_ids)} 个 session 已归档清理 "
        f"(has_null_session={has_null}, non_null_count={len(non_null_ids)})"
    )


async def _cleanup_empty_partitions(conn) -> int:
    """归档完成后清理变空的分区（保留当月、下月和默认分区）"""
    now = _utcnow()
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1

    protected = {
        f"request_details_active_{now.strftime('%Y_%m')}",
        f"request_details_active_{next_year:04d}_{next_month:02d}",
        "request_details_active_default",
    }

    partitions = await _find_partitions(conn)
    cleaned = 0
    for pn in partitions:
        if pn in protected:
            continue
        if await _is_partition_empty(conn, pn):
            try:
                await conn.execute(
                    f"ALTER TABLE public.request_details_active DETACH PARTITION public.{pn}"
                )
                await conn.execute(f"DROP TABLE public.{pn}")
                logger.info(f"已清理空分区: {pn}")
                cleaned += 1
            except Exception as e:
                logger.warning(f"清理空分区 {pn} 失败: {e}")
    return cleaned
