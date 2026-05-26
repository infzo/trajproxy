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
from typing import Any, Dict, List, Optional

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
) -> Dict[str, Any]:
    """按 run_id 粒度归档过期数据

    Args:
        pool: 协调器 DB 连接池（仅用于查询和 cleanup）
        workers: Ray Actor Worker 列表
        storage: 存储后端（协调器用，获取 location_prefix）
        local_temp_path: Worker 临时目录
        retention_days: 保留天数
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
    }

    # 阶段 1: 查找过期 run（短连接，立即释放）
    async with pool.connection() as conn:
        await ensure_current_partition(conn)
        expired_runs = await _find_expired_runs(conn, threshold)
        await conn.commit()

    logger.info(
        f"阈值: {threshold.isoformat()}, "
        f"找到 {len(expired_runs)} 个过期 run"
    )

    # 阶段 2: 每个 run 使用独立连接，互不影响
    total = len(expired_runs)
    for idx, run_id in enumerate(expired_runs, 1):
        logger.info(f"[{idx}/{total}] 开始归档 run '{run_id}'...")
        try:
            stats = await _archive_run(pool, workers, storage, run_id)
            result["runs_processed"] += 1
            result["sessions_archived"] += stats["sessions"]
            result["records_archived"] += stats["records"]
            result["archive_files"].extend(stats["files"])
            result["details"].append({
                "run_id": run_id,
                "sessions": stats["sessions"],
                "records": stats["records"],
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

    return result


async def _archive_run(
    pool,
    workers,
    storage,
    run_id: str,
) -> Dict[str, Any]:
    """归档一个 run：查询 session 列表 → 分发给 Workers → cleanup"""
    safe_run = _safe_path(run_id)
    t_start = time.monotonic()

    # 1. 查询 session 列表（协调器连接，短用即还）
    async with pool.connection() as conn:
        sessions = await _get_run_sessions(conn, run_id)

    if not sessions:
        logger.info(f"  run '{run_id}' 无活跃记录，跳过")
        return {"sessions": 0, "records": 0, "files": []}

    logger.info(f"  run '{run_id}': {len(sessions)} 个 session 待归档")

    # 2. 轮询分发 session 到 Workers
    futures = []
    for i, session_id in enumerate(sessions):
        worker = workers[i % len(workers)]
        futures.append(worker.archive.remote(run_id, session_id))

    # 3. 等待全部完成
    import ray
    raw_results = await asyncio.to_thread(ray.get, futures)

    t_workers = time.monotonic()
    logger.info(
        f"  Workers 完成: {len(sessions)} 个 session, "
        f"耗时 {t_workers - t_start:.1f}s"
    )

    # 4. 收集统计
    total_records = 0
    files = []
    for r in raw_results:
        total_records += r["records"]
        if r["file"] is not None:
            files.append(r["file"])

    # 5. run 级 DELETE + UPDATE（协调器连接）
    archive_location = f"{storage.location_prefix}{safe_run}/"
    async with pool.connection() as conn:
        await _cleanup_run(conn, run_id, archive_location)

    t_done = time.monotonic()
    logger.info(
        f"  归档 run '{run_id}' 完成: {len(files)} 个文件, {total_records} 条记录, "
        f"总耗时 {t_done - t_start:.1f}s "
        f"(Workers {t_workers - t_start:.1f}s / 清理 {t_done - t_workers:.1f}s)"
    )
    return {"sessions": len(files), "records": total_records, "files": files}


async def _cleanup_run(conn, run_id: str, archive_location: str):
    """按 run_id 粒度清理：DELETE details + UPDATE metadata"""
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM request_details_active "
            "WHERE unique_id IN ("
            "  SELECT unique_id FROM request_metadata WHERE run_id = %s"
            ")",
            (run_id,),
        )
        await conn.execute(
            "UPDATE request_metadata "
            "SET archive_location = %s, archived_at = NOW() "
            "WHERE run_id = %s AND archive_location IS NULL",
            (archive_location, run_id),
        )


async def _cleanup_empty_partitions(conn) -> int:
    """归档完成后清理变空的分区"""
    partitions = await _find_partitions(conn)
    cleaned = 0
    for pn in partitions:
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
