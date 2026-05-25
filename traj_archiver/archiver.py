"""
核心归档逻辑 - 按 run_id 粒度将过期数据归档到存储后端

流程:
  1. 查找全部记录已过期的 run_id (MAX(created_at) < threshold)
  2. 按 run 导出所有 session → 每 session 一个 .jsonl.gz
  3. 立即上传到 storage，释放本地磁盘
  4. DELETE + UPDATE metadata 同事务
  5. 顺手清理空的月分区
"""

import gzip
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from psycopg.rows import dict_row

from traj_archiver.storage import Storage

logger = logging.getLogger(__name__)

# 同时打开的最大文件句柄数
MAX_OPEN_FILES = 50
# 每批处理的记录数
BATCH_SIZE = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_path(segment: str) -> str:
    return segment.replace(",", "_").replace("/", "_")


# ============================================================
# 分区管理（业务不变：30天月分区）
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


async def _cleanup_empty_partitions(conn, processed_runs: int):
    """归档完成后，清理变空的分区"""
    if processed_runs == 0:
        return
    partitions = await _find_partitions(conn)
    for pn in partitions:
        if await _is_partition_empty(conn, pn):
            try:
                await conn.execute(
                    f"ALTER TABLE public.request_details_active DETACH PARTITION public.{pn}"
                )
                await conn.execute(f"DROP TABLE public.{pn}")
                logger.info(f"已清理空分区: {pn}")
            except Exception as e:
                logger.warning(f"清理空分区 {pn} 失败: {e}")


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
            WHERE m.run_id IS NOT NULL
            GROUP BY m.run_id
            HAVING MAX(d.created_at) < %s
            ORDER BY m.run_id
        """, (threshold,))
        return [r[0] for r in await cur.fetchall()]


async def _find_expired_null_sessions(conn, threshold: datetime) -> List[str]:
    """找到所有记录都已过期的 run_id=NULL 的 session_id"""
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT m.session_id
            FROM request_details_active d
            JOIN request_metadata m ON d.unique_id = m.unique_id
            WHERE m.run_id IS NULL
            GROUP BY m.session_id
            HAVING MAX(d.created_at) < %s
            ORDER BY m.session_id
        """, (threshold,))
        return [r[0] for r in await cur.fetchall()]


# ============================================================
# 归档执行
# ============================================================


async def archive_details(
    pool,
    storage: Storage,
    local_temp_path: str,
    retention_days: int,
) -> Dict[str, Any]:
    """按 run_id 粒度归档过期数据

    Args:
        pool: 数据库连接池
        storage: 存储实例
        local_temp_path: 本地临时目录
        retention_days: 留存天数
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

    async with pool.connection() as conn:
        await ensure_current_partition(conn)

        expired_runs = await _find_expired_runs(conn, threshold)
        logger.info(
            f"阈值: {threshold.isoformat()}, "
            f"找到 {len(expired_runs)} 个过期 run"
        )

        # 归档有 run_id 的
        for run_id in expired_runs:
            try:
                stats = await _archive_run(conn, storage, temp_root, run_id)
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

        # 归档 run_id=NULL 的（按 session）
        expired_null = await _find_expired_null_sessions(conn, threshold)
        if expired_null:
            logger.info(f"找到 {len(expired_null)} 个过期 NULL-run 的 session")
            for session_id in expired_null:
                try:
                    stats = await _archive_null_session(
                        conn, storage, temp_root, session_id
                    )
                    result["sessions_archived"] += 1
                    result["records_archived"] += stats["records"]
                    if stats["file"]:
                        result["archive_files"].append(stats["file"])
                except Exception as e:
                    msg = f"归档 session '{session_id}' 失败: {e}"
                    logger.error(msg)
                    logger.debug(traceback.format_exc())
                    result["errors"].append(msg)

        # 清理空分区
        cleaned = await _cleanup_empty_partitions_verbose(conn, result["runs_processed"] + len(expired_null))
        result["partitions_cleaned"] = cleaned

    return result


async def _cleanup_empty_partitions_verbose(conn, processed: int) -> int:
    """清理空分区并返回清理数量"""
    if processed == 0:
        return 0
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


async def _archive_run(
    conn, storage: Storage, temp_root: Path, run_id: str,
) -> Dict[str, Any]:
    """归档一个 run 下的所有 session

    流程:
      1. 查询该 run 的全部记录（JOIN metadata 获取 session_id）
      2. 按 session_id 分组，写入临时文件
      3. 每个 session 文件写完立即上传，删除临时文件
      4. 分批 DELETE + UPDATE metadata
    """
    safe_run = _safe_path(run_id)

    # 查询该 run 的所有记录
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("""
            SELECT d.unique_id, d.tokenizer_path, d.messages,
                   d.raw_request, d.raw_response,
                   d.text_request, d.text_response,
                   d.prompt_text, d.token_ids,
                   d.token_request, d.token_response,
                   d.response_text, d.response_ids,
                   d.full_conversation_text, d.full_conversation_token_ids,
                   d.error_traceback, d.created_at,
                   m.session_id
            FROM request_details_active d
            JOIN request_metadata m ON d.unique_id = m.unique_id
            WHERE m.run_id = %s
            ORDER BY m.session_id, d.created_at
        """, (run_id,))
        records = await cur.fetchall()

    if not records:
        logger.info(f"  run '{run_id}' 无记录，跳过")
        return {"sessions": 0, "records": 0, "files": []}

    logger.info(f"  归档 run '{run_id}': {len(records)} 条记录")

    # 按 session_id 分组写入
    session_files: Dict[str, tuple] = {}
    archive_map: Dict[str, str] = {}  # unique_id → archive_location
    uploaded_files = []
    record_count = 0

    for rec in records:
        rec = dict(rec)
        session_id = rec.pop("session_id")
        safe_session = _safe_path(session_id)

        # 打开或获取 session 文件
        if safe_session not in session_files:
            if len(session_files) >= MAX_OPEN_FILES:
                # 关闭最久未使用的文件
                old_key = next(iter(session_files))
                old_file, old_path, old_ids = session_files.pop(old_key)
                old_file.close()
                loc = _finalize_file(storage, old_path, f"{safe_run}/{old_key}.jsonl.gz")
                for uid in old_ids:
                    archive_map[uid] = loc
                uploaded_files.append(loc)

            run_dir = temp_root / safe_run
            run_dir.mkdir(parents=True, exist_ok=True)
            file_path = run_dir / f"{safe_session}.jsonl.gz"
            fh = gzip.open(file_path, "wt", encoding="utf-8")
            session_files[safe_session] = (fh, file_path, [])

        fh, path, uid_list = session_files[safe_session]

        # 写 JSONL
        for key, val in rec.items():
            if isinstance(val, datetime):
                rec[key] = val.isoformat()
        fh.write(json.dumps(rec, default=str) + "\n")
        uid_list.append(rec["unique_id"])
        record_count += 1

    # 关闭剩余文件并上传
    for safe_session, (fh, file_path, uid_list) in session_files.items():
        fh.close()
        key = f"{safe_run}/{safe_session}.jsonl.gz"
        loc = _finalize_file(storage, file_path, key)
        for uid in uid_list:
            archive_map[uid] = loc
        uploaded_files.append(loc)

    # 分批 DELETE + UPDATE
    uid_pairs = list(archive_map.items())
    for i in range(0, len(uid_pairs), BATCH_SIZE):
        batch = uid_pairs[i : i + BATCH_SIZE]
        await _delete_and_update(conn, batch)

    # 清理临时目录
    run_dir = temp_root / safe_run
    for f in run_dir.glob("*.jsonl.gz"):
        f.unlink(missing_ok=True)
    try:
        run_dir.rmdir()
    except OSError:
        pass

    logger.info(f"  归档 run '{run_id}' 完成: {len(uploaded_files)} 个文件, {record_count} 条记录")
    return {"sessions": len(uploaded_files), "records": record_count, "files": uploaded_files}


async def _archive_null_session(
    conn, storage: Storage, temp_root: Path, session_id: str,
) -> Dict[str, Any]:
    """归档一个 run_id=NULL 的 session"""
    safe_session = _safe_path(session_id)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("""
            SELECT d.unique_id, d.tokenizer_path, d.messages,
                   d.raw_request, d.raw_response,
                   d.text_request, d.text_response,
                   d.prompt_text, d.token_ids,
                   d.token_request, d.token_response,
                   d.response_text, d.response_ids,
                   d.full_conversation_text, d.full_conversation_token_ids,
                   d.error_traceback, d.created_at
            FROM request_details_active d
            JOIN request_metadata m ON d.unique_id = m.unique_id
            WHERE m.run_id IS NULL AND m.session_id = %s
            ORDER BY d.created_at
        """, (session_id,))
        records = await cur.fetchall()

    if not records:
        return {"records": 0, "file": None}

    # 写入文件
    file_path = temp_root / f"{safe_session}.jsonl.gz"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    uids = []
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        for rec in records:
            rec = dict(rec)
            for key, val in rec.items():
                if isinstance(val, datetime):
                    rec[key] = val.isoformat()
            f.write(json.dumps(rec, default=str) + "\n")
            uids.append(rec["unique_id"])

    # 上传
    key = f"_unknown/{safe_session}.jsonl.gz"
    loc = _finalize_file(storage, file_path, key)

    # DELETE + UPDATE
    await _delete_and_update(conn, [(uid, loc) for uid in uids])

    logger.info(f"  归档 NULL-run session '{session_id}': {len(uids)} 条记录")
    return {"records": len(uids), "file": loc}


def _finalize_file(storage: Storage, local_path: Path, key: str) -> str:
    """上传文件并清理本地副本"""
    loc = storage.upload(local_path, key)
    local_path.unlink(missing_ok=True)
    return loc


async def _delete_and_update(conn, uid_loc_pairs: List[Tuple[str, str]]):
    """DELETE + UPDATE metadata 在同一事务内"""
    if not uid_loc_pairs:
        return

    uids = [p[0] for p in uid_loc_pairs]

    async with conn.transaction():
        await conn.execute(
            "DELETE FROM request_details_active WHERE unique_id = ANY(%s)",
            (uids,),
        )
        for uid, loc in uid_loc_pairs:
            await conn.execute(
                """UPDATE request_metadata
                   SET archive_location = %s, archived_at = NOW()
                   WHERE unique_id = %s""",
                (loc, uid),
            )
