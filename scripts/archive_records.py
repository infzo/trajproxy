#!/usr/bin/env python3
"""
归档脚本 - 将过期的请求详情分区导出到外部存储

详情表 request_details_active 按月分区，归档时：
1. 导出分区数据到 JSONL+GZIP 文件
2. 更新 request_metadata 的 archive_location
3. DETACH + DROP 分区（瞬间完成，零 VACUUM 压力）

用法:
    python scripts/archive_records.py --retention-days 30
    python scripts/archive_records.py --dry-run --retention-days 30
"""

import argparse
import gzip
import json
import os
import sys
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row


async def archive_details(
    pool: AsyncConnectionPool,
    archive_dir: Path,
    retention_days: int,
    dry_run: bool = False,
):
    """归档过期的详情分区

    按月分区逐个处理：导出 → 更新 metadata → DETACH + DROP。

    Args:
        pool: 数据库连接池
        archive_dir: 归档文件目录
        retention_days: 活跃数据保留天数
        dry_run: 试运行模式，不修改数据库
    """
    threshold = datetime.now() - timedelta(days=retention_days)
    archive_dir.mkdir(parents=True, exist_ok=True)

    async with pool.connection() as conn:
        # 1. 查找所有可归档的分区（created_at 上界 < threshold 的分区）
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT
                    pp.relname AS partition_name,
                    pg_get_expr(pp.relpartbound, pp.oid) AS bound_expr
                FROM pg_class pt
                JOIN pg_inherits i ON pt.oid = i.inhparent
                JOIN pg_class pp ON i.inhrelid = pp.oid
                WHERE pt.relname = 'request_details_active'
                  AND pt.relnamespace = 'public'::regnamespace
                ORDER BY pp.relname
            """)
            partitions = await cur.fetchall()

        if not partitions:
            print("没有找到任何分区")
            return

        print(f"找到 {len(partitions)} 个分区，阈值: {threshold.isoformat()}")

        for part in partitions:
            partition_name = part['partition_name']

            # 从分区名提取月份（格式: request_details_active_YYYY_MM）
            parts = partition_name.rsplit('_', 2)
            if len(parts) < 3:
                print(f"  跳过非标准分区名: {partition_name}")
                continue

            year_str, month_str = parts[-2], parts[-1]
            try:
                month_start = datetime(int(year_str), int(month_str), 1)
                if month_str == '12':
                    month_end = datetime(int(year_str) + 1, 1, 1)
                else:
                    month_end = datetime(int(year_str), int(month_str) + 1, 1)
            except ValueError:
                print(f"  跳过无法解析的分区: {partition_name}")
                continue

            # 只归档整个分区都在阈值之前的（分区上界 <= threshold）
            if month_end > threshold:
                print(f"  跳过活跃分区: {partition_name}（{month_start.date()} ~ {month_end.date()}）")
                continue

            month_label = f"{year_str}_{month_str}"
            archive_filename = f"{month_label}.jsonl.gz"
            archive_path = archive_dir / archive_filename

            print(f"\n处理分区: {partition_name}（{month_start.date()} ~ {month_end.date()}）")

            # 2. 查询该分区关联的 unique_id（用于更新 metadata）
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    SELECT COUNT(*) FROM public.{partition_name}
                """)
                count = (await cur.fetchone())[0]

            if count == 0:
                print(f"  分区为空，跳过")
                if not dry_run:
                    await conn.execute(f"""
                        ALTER TABLE public.request_details_active
                        DETACH PARTITION public.{partition_name}
                    """)
                    await conn.execute(f"DROP TABLE public.{partition_name}")
                    print(f"  已删除空分区")
                continue

            print(f"  分区包含 {count} 条记录")

            # 3. 导出分区数据到 JSONL+GZIP
            if not dry_run or True:  # dry-run 也导出文件以便验证
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

                with gzip.open(archive_path, 'wt') as f:
                    for record in records:
                        record_dict = dict(record)
                        for key, val in record_dict.items():
                            if isinstance(val, datetime):
                                record_dict[key] = val.isoformat()
                        f.write(json.dumps(record_dict, default=str) + '\n')

                print(f"  已导出到 {archive_filename}（{len(records)} 条记录）")

            if dry_run:
                print(f"  [试运行] 将更新 metadata 并 DETACH + DROP 分区 {partition_name}")
                continue

            # 4. 更新 metadata 归档位置
            unique_ids = [r['unique_id'] for r in records]
            async with conn.transaction():
                await conn.execute("""
                    UPDATE public.request_metadata
                    SET archive_location = %s, archived_at = NOW()
                    WHERE unique_id = ANY(%s)
                """, (archive_filename, unique_ids))

            # 5. DETACH + DROP 分区（瞬间完成）
            await conn.execute(f"""
                ALTER TABLE public.request_details_active
                DETACH PARTITION public.{partition_name}
            """)
            await conn.execute(f"DROP TABLE public.{partition_name}")

            print(f"  已归档 {len(unique_ids)} 条记录，分区 {partition_name} 已删除")

    print(f"\n归档完成")


async def ensure_current_partition(pool: AsyncConnectionPool):
    """确保当前月份的分区存在

    每次归档时顺便检查并创建下月分区，避免月底边界问题。

    Args:
        pool: 数据库连接池
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
        1
    )

    async with pool.connection() as conn:
        # 检查并创建当前月分区
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 1 FROM pg_class
                WHERE relname = %s AND relnamespace = 'public'::regnamespace
            """, (partition_name,))
            if not await cur.fetchone():
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS public.{partition_name}
                        PARTITION OF public.request_details_active
                        FOR VALUES FROM ('{month_start.isoformat()}') TO ('{next_month_start.isoformat()}')
                """)
                print(f"已创建当月分区: {partition_name}")

        # 检查并创建下月分区（提前创建）
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 1 FROM pg_class
                WHERE relname = %s AND relnamespace = 'public'::regnamespace
            """, (next_partition_name,))
            if not await cur.fetchone():
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS public.{next_partition_name}
                        PARTITION OF public.request_details_active
                        FOR VALUES FROM ('{next_month_start.isoformat()}') TO ('{next_next_month_start.isoformat()}')
                """)
                print(f"已创建下月分区: {next_partition_name}")

        # 确保默认分区存在（兜底）
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
                print("已创建默认分区: request_details_active_default")


def main():
    parser = argparse.ArgumentParser(description="归档过期的请求详情分区数据")
    parser.add_argument(
        "--retention-days", type=int, default=30,
        help="活跃数据保留天数（默认: 30）"
    )
    parser.add_argument(
        "--archive-dir", type=str, default="/data/archives",
        help="归档文件目录（默认: /data/archives）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="试运行模式，导出文件但不修改数据库"
    )
    parser.add_argument(
        "--database-url", type=str,
        default=os.environ.get("DATABASE_URL", ""),
        help="数据库连接 URL（默认读取 DATABASE_URL 环境变量）"
    )

    args = parser.parse_args()

    if not args.database_url:
        print("错误: 请设置 DATABASE_URL 环境变量或通过 --database-url 指定")
        sys.exit(1)

    archive_dir = Path(args.archive_dir)

    print("=== 归档配置 ===")
    print(f"  保留天数: {args.retention_days}")
    print(f"  归档目录: {archive_dir}")
    print(f"  试运行: {args.dry_run}")
    print()

    async def run():
        pool = AsyncConnectionPool(
            conninfo=args.database_url,
            min_size=1,
            max_size=5,
            timeout=30
        )
        await pool.open()
        try:
            # 确保分区存在
            await ensure_current_partition(pool)
            # 执行归档
            await archive_details(
                pool=pool,
                archive_dir=archive_dir,
                retention_days=args.retention_days,
                dry_run=args.dry_run,
            )
        finally:
            await pool.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
