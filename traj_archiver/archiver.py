"""
核心归档逻辑 - 按 run_id 粒度将过期数据归档到存储后端

流程:
  1. 查找全部记录已过期的 run_id (MAX(created_at) < threshold)
  2. 按 run 导出所有 session → 每 session 一个 .jsonl.gz
  3. 生产者-消费者 pipeline：写完一个 session 立即上传，释放本地磁盘
  4. DELETE + UPDATE metadata 同事务
  5. 顺手清理空的月分区
"""

import asyncio
import gzip
import json
import logging
import shutil
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

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
            WHERE m.run_id IS NOT NULL AND m.run_id != ''
            GROUP BY m.run_id
            HAVING MAX(d.created_at) < %s
            ORDER BY m.run_id
        """, (threshold,))
        return [r[0] for r in await cur.fetchall()]


# ============================================================
# 上传消费者协程
# ============================================================

# session_id 为 NULL 时使用的占位目录名
_NULL_SESSION_DIR = "_null_session"


class _ConsumerError:
    """消费者异常容器，跨协程传递第一个上传失败"""
    def __init__(self):
        self.exception: Optional[Exception] = None


async def _upload_consumer(
    queue: asyncio.Queue,
    storage,
    results: List[str],
    error_holder: _ConsumerError,
):
    """从队列取文件，异步上传并删除本地临时文件

    上传失败时存下异常到 error_holder 并继续消费，
    避免 producer 因队列满而阻塞死锁。
    """
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break

        file_path, key = item
        try:
            loc = await asyncio.to_thread(storage.upload, file_path, key)
            file_path.unlink(missing_ok=True)
            results.append(loc)
        except Exception as e:
            if error_holder.exception is None:
                error_holder.exception = e
            file_path.unlink(missing_ok=True)
        queue.task_done()


# ============================================================
# 归档执行
# ============================================================


async def archive_details(
    pool,
    storage,
    local_temp_path: str,
    retention_days: int,
    upload_concurrency: int = 1,
    compress: bool = True,
    upload_queue_size: int = 3,
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
                stats = await _archive_run(
                    conn, storage, temp_root, run_id,
                    upload_concurrency, compress, upload_queue_size,
                )
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

        # 清理空分区
        cleaned = await _cleanup_empty_partitions(conn, result["runs_processed"])
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
    compress: bool = True,
    upload_queue_size: int = 3,
) -> Dict[str, Any]:
    """归档一个 run 下的所有 session（生产者-消费者 pipeline）

    利用 ORDER BY session_id 的连续性，写完一个 session 立即提交上传。
    有界队列提供背压，限制磁盘上同时存在的临时文件数量。
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
    run_dir = temp_root / safe_run
    suffix = ".jsonl.gz" if compress else ".jsonl"
    record_count = 0

    # 有界上传队列：队列满时 producer 阻塞，提供背压
    queue_maxsize = upload_queue_size if upload_queue_size > 0 else 0
    upload_queue: asyncio.Queue[Optional[Tuple[Path, str]]] = asyncio.Queue(maxsize=queue_maxsize)
    uploaded_files: List[str] = []
    error_holder = _ConsumerError()

    # 启动多个上传消费者协程
    num_consumers = max(1, upload_concurrency)
    consumer_tasks = [
        asyncio.create_task(
            _upload_consumer(upload_queue, storage, uploaded_files, error_holder)
        )
        for _ in range(num_consumers)
    ]

    # ---- 生产者：流式读取 + 按 session 写文件 ----
    current_session: Optional[str] = None
    current_safe_session: Optional[str] = None
    current_fh = None
    current_path: Optional[Path] = None
    session_count = 0

    def _check_consumer_error():
        if error_holder.exception is not None:
            raise error_holder.exception

    try:
        while True:
            async with conn.cursor() as cur:
                await cur.execute(f'FETCH {BATCH_SIZE} FROM "{cursor_name}"')
                batch = await cur.fetchall()
            if not batch:
                break

            for row in batch:
                rec = dict(zip(_columns, row))
                session_id = rec.pop("session_id")

                # session_id 为 NULL 时使用占位名
                display_session = session_id or _NULL_SESSION_DIR

                # session 切换：关闭前一个 session，提交上传
                if display_session != current_session:
                    if current_fh is not None:
                        current_fh.close()
                        _check_consumer_error()
                        await upload_queue.put((current_path, f"{safe_run}/{current_safe_session}{suffix}"))
                        session_count += 1

                    # 打开新 session 文件
                    current_session = display_session
                    current_safe_session = _safe_path(display_session)
                    run_dir.mkdir(parents=True, exist_ok=True)
                    current_path = run_dir / f"{current_safe_session}{suffix}"
                    current_fh = (
                        gzip.open(current_path, "wt", encoding="utf-8")
                        if compress
                        else open(current_path, "wt", encoding="utf-8")
                    )

                # 写入记录
                for k, v in rec.items():
                    if isinstance(v, datetime):
                        rec[k] = v.isoformat()
                current_fh.write(json.dumps(rec, default=str) + "\n")
                record_count += 1

            logger.info(f"  已处理 {record_count} 条记录...")

        # 关闭 cursor
        async with conn.cursor() as cur:
            await cur.execute(f'CLOSE "{cursor_name}"')

        # 显式提交读事务，否则后续 cleanup 的 conn.transaction() 只会创建 SAVEPOINT
        await conn.commit()

        if record_count == 0:
            logger.info(f"  run '{run_id}' 无记录，跳过")
            # 停止所有消费者
            for _ in consumer_tasks:
                await upload_queue.put(None)
            await asyncio.gather(*consumer_tasks, return_exceptions=True)
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)
            return {"sessions": 0, "records": 0, "files": []}

        # 提交最后一个 session
        if current_fh is not None:
            current_fh.close()
            _check_consumer_error()
            await upload_queue.put((current_path, f"{safe_run}/{current_safe_session}{suffix}"))
            session_count += 1

        t_write = time.monotonic()
        logger.info(
            f"  写入完成: {session_count} 个 session, {record_count} 条记录, "
            f"耗时 {t_write - t_start:.1f}s"
        )

        # 发送 sentinel 停止所有消费者，等待上传完成
        for _ in consumer_tasks:
            await upload_queue.put(None)
        await asyncio.gather(*consumer_tasks, return_exceptions=True)

        # 检查消费者上传是否全部成功
        _check_consumer_error()

        t_upload = time.monotonic()
        logger.info(
            f"  上传完成: {len(uploaded_files)} 个文件, "
            f"耗时 {t_upload - t_write:.1f}s"
        )

        # ---- 清理阶段：run 级 DELETE + UPDATE ----
        archive_location = f"{storage.location_prefix}{safe_run}/"
        await _cleanup_run(conn, run_id, archive_location)

        t_delete = time.monotonic()
        logger.info(
            f"  归档 run '{run_id}' 完成: {len(uploaded_files)} 个文件, {record_count} 条记录, "
            f"总耗时 {t_delete - t_start:.1f}s "
            f"(读取+压缩 {t_write - t_start:.1f}s / 上传 {t_upload - t_write:.1f}s / 清理 {t_delete - t_upload:.1f}s)"
        )
        return {"sessions": len(uploaded_files), "records": record_count, "files": uploaded_files}

    except Exception:
        # 取消所有消费者
        for t in consumer_tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*consumer_tasks, return_exceptions=True)
        raise

    finally:
        if current_fh is not None:
            try:
                current_fh.close()
            except Exception:
                pass
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)


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
