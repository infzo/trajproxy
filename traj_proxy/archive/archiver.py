"""
归档执行器 - 实际执行归档逻辑

复用 scripts/archive_records.py 的核心逻辑，封装为可调用的模块。
"""

import gzip
import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


async def archive_details(
    pool,
    archive_dir: str,
    retention_days: int,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """归档过期的详情分区

    按月分区逐个处理：导出 → 更新 metadata → DETACH + DROP。

    Args:
        pool: 数据库连接池
        archive_dir: 归档文件目录
        retention_days: 活跃数据保留天数
        batch_size: 每批处理的记录数（暂未使用，预留给大批量场景）
        dry_run: 试运行模式，不修改数据库

    Returns:
        归档结果统计
    """
    threshold = datetime.now() - timedelta(days=retention_days)
    archive_path = Path(archive_dir)
    archive_path.mkdir(parents=True, exist_ok=True)

    result = {
        "partitions_processed": 0,
        "records_archived": 0,
        "partitions_dropped": 0,
        "archive_files": [],
        "errors": [],
    }

    async with pool.connection() as conn:
        # 1. 确保分区存在（当前月和下月）
        await ensure_current_partition(conn)

        # 2. 查找所有可归档的分区
        partitions = await _find_archivable_partitions(conn)

        if not partitions:
            logger.info("没有找到任何分区")
            return result

        logger.info(f"找到 {len(partitions)} 个分区，阈值: {threshold.isoformat()}")

        for part in partitions:
            partition_name = part["partition_name"]

            # 从分区名提取月份（格式: request_details_active_YYYY_MM）
            month_info = _parse_partition_name(partition_name)
            if not month_info:
                logger.warning(f"跳过非标准分区名: {partition_name}")
                continue

            month_start, month_end = month_info

            # 只归档整个分区都在阈值之前的（分区上界 <= threshold）
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
                    archive_path=archive_path,
                    dry_run=dry_run,
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


async def ensure_current_partition(conn):
    """确保当前月份的分区存在

    每次归档时顺便检查并创建下月分区，避免月底边界问题。

    Args:
        conn: 数据库连接
    """
    now = datetime.now()
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1

    partition_name = f"request_details_active_{now.strftime('%Y_%m')}"
    next_partition_name = f"request_details_active_{next_year:04d}_{next_month:02d}"

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = datetime(next_year, next_month, 1)
    next_next_month_start = datetime(
        next_year + (1 if next_month == 12 else 0),
        next_month + 1 if next_month < 12 else 1,
        1,
    )

    # 检查并创建当前月分区
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 1 FROM pg_class
            WHERE relname = %s AND relnamespace = 'public'::regnamespace
            """,
            (partition_name,),
        )
        if not await cur.fetchone():
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS public.{partition_name}
                    PARTITION OF public.request_details_active
                    FOR VALUES FROM ('{month_start.isoformat()}') TO ('{next_month_start.isoformat()}')
            """)
            logger.info(f"已创建当月分区: {partition_name}")

    # 检查并创建下月分区（提前创建）
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 1 FROM pg_class
            WHERE relname = %s AND relnamespace = 'public'::regnamespace
            """,
            (next_partition_name,),
        )
        if not await cur.fetchone():
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS public.{next_partition_name}
                    PARTITION OF public.request_details_active
                    FOR VALUES FROM ('{next_month_start.isoformat()}') TO ('{next_next_month_start.isoformat()}')
            """)
            logger.info(f"已创建下月分区: {next_partition_name}")

    # 确保默认分区存在（兜底）
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


async def _find_archivable_partitions(conn) -> List[Dict]:
    """查找所有可归档的分区

    Args:
        conn: 数据库连接

    Returns:
        分区信息列表，每个元素包含 partition_name 和 bound_expr
    """
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
    """解析分区名，返回月份范围

    Args:
        partition_name: 分区名，格式: request_details_active_YYYY_MM

    Returns:
        (month_start, month_end) 或 None（解析失败）
    """
    parts = partition_name.rsplit("_", 2)
    if len(parts) < 3:
        return None

    year_str, month_str = parts[-2], parts[-1]
    try:
        month_start = datetime(int(year_str), int(month_str), 1)
        if month_str == "12":
            month_end = datetime(int(year_str) + 1, 1, 1)
        else:
            month_end = datetime(int(year_str), int(month_str) + 1, 1)
        return month_start, month_end
    except ValueError:
        return None


async def _archive_partition(
    conn,
    partition_name: str,
    month_start: datetime,
    month_end: datetime,
    archive_path: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    """归档单个分区

    Args:
        conn: 数据库连接
        partition_name: 分区名
        month_start: 分区起始时间
        month_end: 分区结束时间
        archive_path: 归档目录
        dry_run: 是否试运行

    Returns:
        归档结果
    """
    year_month = month_start.strftime("%Y_%m")
    archive_filename = f"{year_month}.jsonl.gz"
    archive_file = archive_path / archive_filename

    logger.info(
        f"处理分区: {partition_name} "
        f"({month_start.date()} ~ {month_end.date()})"
    )

    # 1. 查询分区记录数
    async with conn.cursor() as cur:
        await cur.execute(f"SELECT COUNT(*) FROM public.{partition_name}")
        count = (await cur.fetchone())[0]

    if count == 0:
        logger.info(f"  分区为空，跳过导出")
        if not dry_run:
            await conn.execute(
                f"ALTER TABLE public.request_details_active "
                f"DETACH PARTITION public.{partition_name}"
            )
            await conn.execute(f"DROP TABLE public.{partition_name}")
            logger.info(f"  已删除空分区")
        return {
            "records": 0,
            "archive_file": None,
            "dropped": not dry_run,
        }

    logger.info(f"  分区包含 {count} 条记录")

    # 2. 导出分区数据到 JSONL+GZIP
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

    with gzip.open(archive_file, "wt", encoding="utf-8") as f:
        for record in records:
            record_dict = dict(record)
            for key, val in record_dict.items():
                if isinstance(val, datetime):
                    record_dict[key] = val.isoformat()
            f.write(json.dumps(record_dict, default=str) + "\n")

    logger.info(f"  已导出到 {archive_filename}（{len(records)} 条记录）")

    if dry_run:
        logger.info(f"  [试运行] 将更新 metadata 并 DETACH + DROP 分区 {partition_name}")
        return {
            "records": len(records),
            "archive_file": str(archive_file),
            "dropped": False,
        }

    # 3. 更新 metadata 归档位置
    unique_ids = [r["unique_id"] for r in records]
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE public.request_metadata
            SET archive_location = %s, archived_at = NOW()
            WHERE unique_id = ANY(%s)
            """,
            (archive_filename, unique_ids),
        )

    # 4. DETACH + DROP 分区（瞬间完成）
    await conn.execute(
        f"ALTER TABLE public.request_details_active "
        f"DETACH PARTITION public.{partition_name}"
    )
    await conn.execute(f"DROP TABLE public.{partition_name}")

    logger.info(f"  已归档 {len(unique_ids)} 条记录，分区 {partition_name} 已删除")

    return {
        "records": len(records),
        "archive_file": str(archive_file),
        "dropped": True,
    }
