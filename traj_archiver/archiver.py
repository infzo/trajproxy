"""
核心归档逻辑 - 将过期的请求详情分区归档到 S3

流程：查询过期分区 → 导出到本地临时文件 → 上传 S3
      → 更新 metadata.archive_location → DETACH+DROP → 清理本地文件
"""

import gzip
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from psycopg.rows import dict_row

from traj_archiver.s3_storage import S3Storage

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_current_partition(conn):
    """确保当前月份和下月分区存在

    处理默认分区数据冲突（升级部署场景）。
    """
    now = _utcnow()
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = datetime(next_year, next_month, 1, tzinfo=timezone.utc)
    next_next_month_start = datetime(
        next_year + (1 if next_month == 12 else 0),
        next_month + 1 if next_month < 12 else 1,
        1,
        tzinfo=timezone.utc,
    )

    partition_name = f"request_details_active_{now.strftime('%Y_%m')}"
    next_partition_name = f"request_details_active_{next_year:04d}_{next_month:02d}"

    # 当月分区
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relnamespace = 'public'::regnamespace",
            (partition_name,),
        )
        if not await cur.fetchone():
            await _create_partition(conn, partition_name, month_start, next_month_start)

    # 下月分区
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relnamespace = 'public'::regnamespace",
            (next_partition_name,),
        )
        if not await cur.fetchone():
            await _create_partition(conn, next_partition_name, next_month_start, next_next_month_start)

    # 默认分区（兜底）
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 1 FROM pg_class
            WHERE relname = 'request_details_active_default'
              AND relnamespace = 'public'::regnamespace
            """
        )
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

        # 默认分区包含冲突数据：DETACH → 创建 → 迁移 → ATTACH
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


async def _find_archivable_partitions(conn) -> List[Dict]:
    """查找所有可归档的分区"""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT
                pp.relname AS partition_name,
                pg_get_expr(pp.relpartbound, pp.oid) AS bound_expr
            FROM pg_class pt
            JOIN pg_inherits i ON pt.oid = i.inhparent
            JOIN pg_class pp ON i.inhrelid = pp.oid
            WHERE pt.relname = 'request_details_active'
              AND pt.relnamespace = 'public'::regnamespace
            ORDER BY pp.relname
            """
        )
        return await cur.fetchall()


def _parse_partition_name(partition_name: str) -> Optional[tuple]:
    """解析分区名，返回 (month_start, month_end) 或 None"""
    parts = partition_name.rsplit("_", 2)
    if len(parts) < 3:
        return None

    year_str, month_str = parts[-2], parts[-1]
    try:
        month_start = datetime(int(year_str), int(month_str), 1, tzinfo=timezone.utc)
        if month_str == "12":
            month_end = datetime(int(year_str) + 1, 1, 1, tzinfo=timezone.utc)
        else:
            month_end = datetime(int(year_str), int(month_str) + 1, 1, tzinfo=timezone.utc)
        return month_start, month_end
    except ValueError:
        return None


async def archive_details(
    pool,
    s3_storage: S3Storage,
    local_temp_path: str,
    retention_days: int,
) -> Dict[str, Any]:
    """归档过期的详情分区到 S3

    Args:
        pool: 数据库连接池
        s3_storage: S3 存储实例
        local_temp_path: 本地临时导出目录
        retention_days: 活跃数据保留天数

    Returns:
        归档结果统计
    """
    threshold = _utcnow() - timedelta(days=retention_days)
    temp_path = Path(local_temp_path)
    temp_path.mkdir(parents=True, exist_ok=True)

    result = {
        "partitions_processed": 0,
        "records_archived": 0,
        "partitions_dropped": 0,
        "archive_files": [],
        "errors": [],
    }

    async with pool.connection() as conn:
        # 确保分区存在
        await ensure_current_partition(conn)

        # 查找可归档分区
        partitions = await _find_archivable_partitions(conn)

        if not partitions:
            logger.info("没有找到任何分区")
            return result

        logger.info(f"找到 {len(partitions)} 个分区，阈值: {threshold.isoformat()}")

        for part in partitions:
            partition_name = part["partition_name"]

            month_info = _parse_partition_name(partition_name)
            if not month_info:
                logger.warning(f"跳过非标准分区名: {partition_name}")
                continue

            month_start, month_end = month_info

            # 只归档整个分区都在阈值之前的
            if month_end > threshold:
                logger.info(
                    f"跳过活跃分区: {partition_name} "
                    f"({month_start.date()} ~ {month_end.date()})"
                )
                continue

            try:
                partition_result = await _archive_partition(
                    conn=conn,
                    partition_name=partition_name,
                    month_start=month_start,
                    month_end=month_end,
                    temp_path=temp_path,
                    s3_storage=s3_storage,
                )

                result["partitions_processed"] += 1
                result["records_archived"] += partition_result["records"]
                if partition_result["archive_file"]:
                    result["archive_files"].append(partition_result["archive_file"])
                if partition_result["dropped"]:
                    result["partitions_dropped"] += 1

            except Exception as e:
                error_msg = f"归档分区 {partition_name} 失败: {str(e)}"
                logger.error(error_msg)
                logger.debug(traceback.format_exc())
                result["errors"].append(error_msg)

    return result


async def _archive_partition(
    conn,
    partition_name: str,
    month_start: datetime,
    month_end: datetime,
    temp_path: Path,
    s3_storage: S3Storage,
) -> Dict[str, Any]:
    """归档单个分区：导出 → 上传 S3 → 更新 metadata → DETACH+DROP"""
    year_month = month_start.strftime("%Y_%m")
    archive_filename = f"{year_month}.jsonl.gz"
    local_file = temp_path / archive_filename

    logger.info(
        f"处理分区: {partition_name} ({month_start.date()} ~ {month_end.date()})"
    )

    # 1. 查询分区记录数
    async with conn.cursor() as cur:
        await cur.execute(f"SELECT COUNT(*) FROM public.{partition_name}")
        count = (await cur.fetchone())[0]

    if count == 0:
        logger.info("  分区为空，直接删除")
        await conn.execute(
            f"ALTER TABLE public.request_details_active "
            f"DETACH PARTITION public.{partition_name}"
        )
        await conn.execute(f"DROP TABLE public.{partition_name}")
        logger.info(f"  已删除空分区: {partition_name}")
        return {"records": 0, "archive_file": None, "dropped": True}

    logger.info(f"  分区包含 {count} 条记录")

    # 2. 导出到本地临时文件
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"""
            SELECT d.unique_id, d.tokenizer_path, d.messages,
                   d.raw_request, d.raw_response,
                   d.text_request, d.text_response,
                   d.prompt_text, d.token_ids,
                   d.token_request, d.token_response,
                   d.response_text, d.response_ids,
                   d.full_conversation_text, d.full_conversation_token_ids,
                   d.error_traceback, d.created_at
            FROM public.{partition_name} d
            ORDER BY d.created_at
        """)
        records = await cur.fetchall()

    with gzip.open(local_file, "wt", encoding="utf-8") as f:
        for record in records:
            record_dict = dict(record)
            for key, val in record_dict.items():
                if isinstance(val, datetime):
                    record_dict[key] = val.isoformat()
            f.write(json.dumps(record_dict, default=str) + "\n")

    logger.info(f"  已导出到 {archive_filename}（{len(records)} 条记录）")

    # 3. 上传到 S3
    s3_uri = s3_storage.upload_file(local_file, archive_filename)
    logger.info(f"  已上传到 S3: {s3_uri}")

    # 4. 更新 metadata 归档位置
    unique_ids = [r["unique_id"] for r in records]
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE public.request_metadata
            SET archive_location = %s, archived_at = NOW()
            WHERE unique_id = ANY(%s)
            """,
            (s3_uri, unique_ids),
        )

    # 5. DETACH + DROP 分区
    await conn.execute(
        f"ALTER TABLE public.request_details_active "
        f"DETACH PARTITION public.{partition_name}"
    )
    await conn.execute(f"DROP TABLE public.{partition_name}")

    # 6. 清理本地临时文件
    local_file.unlink(missing_ok=True)

    logger.info(f"  已归档 {len(unique_ids)} 条记录，分区 {partition_name} 已删除")

    return {"records": len(records), "archive_file": s3_uri, "dropped": True}
