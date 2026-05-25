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
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from psycopg.rows import dict_row

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
    storage,
    local_temp_path: str,
    retention_days: int,
    upload_concurrency: int = 1,
) -> Dict[str, Any]:
    """按 run_id 粒度归档过期数据"""
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
        total = len(expired_runs)
        for idx, run_id in enumerate(expired_runs, 1):
            logger.info(f"[{idx}/{total}] 开始归档 run '{run_id}'...")
            try:
                stats = await _archive_run(conn, storage, temp_root, run_id, upload_concurrency)
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
        cleaned = await _cleanup_empty_partitions(conn, result["runs_processed"] + len(expired_null))
        result["partitions_cleaned"] = cleaned

    return result


async def _cleanup_empty_partitions(conn, processed: int) -> int:
    """归档完成后清理变空的分区"""
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
    conn, storage, temp_root: Path, run_id: str,
    upload_concurrency: int = 1,
) -> Dict[str, Any]:
    """归档一个 run 下的所有 session

    流程:
      1. server-side cursor 流式读取记录
      2. 按 session_id 分组写入 gzip 文件
      3. 并发上传所有 session 文件
      4. 全部上传成功后 DELETE + UPDATE metadata
    """
    safe_run = _safe_path(run_id)

    _columns = [
        "unique_id", "tokenizer_path", "messages",
        "raw_request", "raw_response", "text_request", "text_response",
        "prompt_text", "token_ids", "token_request", "token_response",
        "response_text", "response_ids",
        "full_conversation_text", "full_conversation_token_ids",
        "error_traceback", "created_at", "session_id",
    ]
    cursor_name = f"arch_{safe_run}"

    async with conn.cursor() as cur:
        await cur.execute(f"""
            DECLARE "{cursor_name}" CURSOR FOR
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

    logger.info(f"  归档 run '{run_id}'...")
    t_start = time.monotonic()

    # ---- 写入阶段：流式读取 + 按 session 写 gzip ----
    session_files: Dict[str, tuple] = {}
    pending_uploads = []  # [(file_path, upload_key, uid_list)]
    record_count = 0

    while True:
        async with conn.cursor() as cur:
            await cur.execute(f'FETCH {BATCH_SIZE} FROM "{cursor_name}"')
            batch = await cur.fetchall()
        if not batch:
            break

        for row in batch:
            rec = dict(zip(_columns, row))
            session_id = rec.pop("session_id")
            safe_session = _safe_path(session_id)

            if safe_session not in session_files:
                if len(session_files) >= MAX_OPEN_FILES:
                    old_key = next(iter(session_files))
                    old_fh, old_path, old_ids = session_files.pop(old_key)
                    old_fh.close()
                    pending_uploads.append((old_path, f"{safe_run}/{old_key}.jsonl.gz", old_ids))

                run_dir = temp_root / safe_run
                run_dir.mkdir(parents=True, exist_ok=True)
                file_path = run_dir / f"{safe_session}.jsonl.gz"
                fh = gzip.open(file_path, "wt", encoding="utf-8")
                session_files[safe_session] = (fh, file_path, [])

            fh, _, uid_list = session_files[safe_session]
            for k, v in rec.items():
                if isinstance(v, datetime):
                    rec[k] = v.isoformat()
            fh.write(json.dumps(rec, default=str) + "\n")
            uid_list.append(rec["unique_id"])
            record_count += 1

        logger.info(f"  已处理 {record_count} 条记录...")

    async with conn.cursor() as cur:
        await cur.execute(f'CLOSE "{cursor_name}"')

    if record_count == 0:
        logger.info(f"  run '{run_id}' 无记录，跳过")
        return {"sessions": 0, "records": 0, "files": []}

    # 关闭剩余文件，加入待上传队列
    for safe_session, (fh, file_path, uid_list) in session_files.items():
        fh.close()
        pending_uploads.append((file_path, f"{safe_run}/{safe_session}.jsonl.gz", uid_list))

    t_write = time.monotonic()
    logger.info(f"  写入完成: {len(pending_uploads)} 个文件, {record_count} 条记录, 耗时 {t_write - t_start:.1f}s")

    # ---- 上传阶段：并发上传所有 session 文件 ----
    archive_map: Dict[str, str] = {}
    uploaded_files = []

    for loc, uid_list in _upload_pending(storage, pending_uploads, upload_concurrency):
        for uid in uid_list:
            archive_map[uid] = loc
        uploaded_files.append(loc)

    t_upload = time.monotonic()
    logger.info(f"  上传完成: {len(uploaded_files)} 个文件, 耗时 {t_upload - t_write:.1f}s")

    # ---- 删除阶段：全部上传成功后才执行 ----
    uid_pairs = list(archive_map.items())
    for i in range(0, len(uid_pairs), BATCH_SIZE):
        await _delete_and_update(conn, uid_pairs[i:i + BATCH_SIZE])

    t_delete = time.monotonic()
    logger.info(
        f"  归档 run '{run_id}' 完成: {len(uploaded_files)} 个文件, {record_count} 条记录, "
        f"总耗时 {t_delete - t_start:.1f}s "
        f"(读取+压缩 {t_write - t_start:.1f}s / 上传 {t_upload - t_write:.1f}s / 删除 {t_delete - t_upload:.1f}s)"
    )
    return {"sessions": len(uploaded_files), "records": record_count, "files": uploaded_files}


async def _archive_null_session(
    conn, storage, temp_root: Path, session_id: str,
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


def _upload_pending(storage, pending_uploads, concurrency):
    """并发上传待处理文件，返回 [(location, uid_list)]"""
    if not pending_uploads:
        return []

    if concurrency <= 1:
        results = []
        for file_path, key, uid_list in pending_uploads:
            loc = storage.upload(file_path, key)
            file_path.unlink(missing_ok=True)
            results.append((loc, uid_list))
        return results

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _upload_one(file_path, key):
        loc = storage.upload(file_path, key)
        file_path.unlink(missing_ok=True)
        return loc

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {}
        for file_path, key, uid_list in pending_uploads:
            f = pool.submit(_upload_one, file_path, key)
            futures[f] = uid_list

        for f in as_completed(futures):
            results.append((f.result(), futures[f]))

    return results


def _finalize_file(storage, local_path: Path, key: str) -> str:
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
