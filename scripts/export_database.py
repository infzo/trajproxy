#!/usr/bin/env python3
"""
数据库导出脚本

一键导出 PostgreSQL 数据库中所有数据，按 run_id 组织目录结构：

    export_output/
      <run_id_1>/
        models.json                  # 模型配置（JSON）
        request_metadata.parquet     # 请求元数据（Parquet，zstd 压缩）
        request_details.parquet      # 请求详情（Parquet，zstd 压缩）
        archived_trace.json          # 已归档记录追踪（如有）
      <run_id_2>/
        ...
      __global__/
        models.json                  # 全局模型（run_id 为空字符串）
      export_summary.json            # 导出摘要

用法：
    python scripts/export_database.py
    python scripts/export_database.py -o /path/to/export
    python scripts/export_database.py --run-id app-001
    python scripts/export_database.py --db-url postgresql://user:pass@host:5432/db

依赖：psycopg, orjson, pyyaml, pyarrow
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import orjson
import pyarrow as pa
import pyarrow.parquet as pq
import psycopg
import yaml


# ─── 配置加载 ───────────────────────────────────────────────


def get_database_url(args_db_url: Optional[str] = None) -> str:
    """获取数据库连接 URL

    优先级：命令行参数 → DATABASE_URL 环境变量 → configs/config.yaml → 默认值
    """
    if args_db_url:
        return args_db_url

    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    config_path = Path(__file__).resolve().parents[1] / "configs" / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        db_url = config.get("database", {}).get("url")
        if db_url:
            return db_url

    default_url = "postgresql://llmproxy:dbpassword9090@localhost:5432/traj_proxy"
    print(f"[WARN] 未找到数据库配置，使用默认值: {default_url}")
    return default_url


# ─── 数据库查询 ───────────────────────────────────────────────


def get_all_run_ids(conn) -> list[str]:
    """获取所有 run_id（合并 model_registry 和 request_metadata），去重排序"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT run_id FROM model_registry
            UNION
            SELECT DISTINCT run_id FROM request_metadata
            ORDER BY run_id
        """)
        return sorted(set(row[0] for row in cur.fetchall()))


def fetch_models_by_run_id(conn, run_id: str) -> list[dict]:
    """查询指定 run_id 的模型配置"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT run_id, model_name, url, api_key, tokenizer_path,
                   token_in_token_out, tool_parser, reasoning_parser, updated_at
            FROM model_registry
            WHERE run_id = %s
            ORDER BY model_name
        """, (run_id,))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    models = []
    for row in rows:
        model = {}
        for col, val in zip(columns, row):
            if isinstance(val, datetime):
                val = val.isoformat()
            model[col] = val
        models.append(model)
    return models


def fetch_request_metadata_by_run_id(conn, run_id: str) -> tuple[list[str], list[tuple]]:
    """查询指定 run_id 的请求元数据，返回 (列名列表, 行列表)"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, unique_id, request_id, session_id, run_id, model,
                   prompt_tokens, completion_tokens, total_tokens, cache_hit_tokens,
                   processing_duration_ms, start_time, end_time, created_at,
                   error, archive_location, archived_at
            FROM request_metadata
            WHERE run_id = %s
            ORDER BY start_time
        """, (run_id,))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
    return columns, rows


def fetch_request_details_by_unique_ids(conn, unique_ids: list[str]) -> tuple[list[str], list[tuple]]:
    """批量查询请求详情，分块查询避免 SQL 参数过多"""
    if not unique_ids:
        return [], []

    chunk_size = 500
    all_rows = []
    columns = None

    with conn.cursor() as cur:
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            cur.execute("""
                SELECT unique_id, created_at, tokenizer_path,
                       messages, raw_request, raw_response,
                       text_request, text_response,
                       prompt_text, token_ids, token_request, token_response,
                       response_text, response_ids,
                       full_conversation_text, full_conversation_token_ids,
                       error_traceback
                FROM request_details_active
                WHERE unique_id = ANY(%s)
                ORDER BY created_at
            """, (chunk,))
            if columns is None:
                columns = [desc[0] for desc in cur.description]
            all_rows.extend(cur.fetchall())

    return columns, all_rows


# ─── 数据转换与写入 ───────────────────────────────────────────────


def row_to_dict(columns: list[str], row: tuple) -> dict:
    """将单行数据库记录转为字典，处理 datetime 序列化"""
    item = {}
    for col, val in zip(columns, row):
        if isinstance(val, datetime):
            val = val.isoformat()
        item[col] = val
    return item


def write_json(data: list[dict], path: Path) -> None:
    """使用 orjson 写入 JSON 文件"""
    with open(path, "wb") as f:
        f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))


def dicts_to_parquet(data: list[dict], path: Path, compression: str = "zstd") -> None:
    """将字典列表写入 Parquet 文件

    使用 pyarrow 直接构建表并写入，zstd 压缩算法兼顾压缩率和速度。
    对于 JSONB/ARRAY 类型列，使用 string 类型存储（orjson 序列化后写入）。
    """
    if not data:
        pq.write_table(pa.table({}), str(path), compression=compression)
        return

    # 按列构建，推断各列类型
    col_names = list(data[0].keys())
    arrays = []
    fields = []

    for col in col_names:
        values = [row.get(col) for row in data]

        # 判断列类型：采样第一个非 None 值
        sample = next((v for v in values if v is not None), None)

        if sample is None:
            # 全为 None 的列，用 string 类型
            arrays.append(pa.array([str(v) if v is not None else None for v in values], type=pa.string()))
            fields.append(pa.field(col, pa.string()))
        elif isinstance(sample, bool):
            arrays.append(pa.array(values, type=pa.bool_()))
            fields.append(pa.field(col, pa.bool_()))
        elif isinstance(sample, int):
            arrays.append(pa.array(values, type=pa.int64()))
            fields.append(pa.field(col, pa.int64()))
        elif isinstance(sample, float):
            arrays.append(pa.array(values, type=pa.float64()))
            fields.append(pa.field(col, pa.float64()))
        elif isinstance(sample, str):
            arrays.append(pa.array(values, type=pa.string()))
            fields.append(pa.field(col, pa.string()))
        elif isinstance(sample, (list, dict)):
            # JSONB/ARRAY 类型：orjson 序列化为字符串存储
            serialized = [
                orjson.dumps(v).decode("utf-8") if v is not None else None
                for v in values
            ]
            arrays.append(pa.array(serialized, type=pa.string()))
            fields.append(pa.field(col, pa.string()))
        else:
            # 其他类型（datetime 等，已在 row_to_dict 中转为字符串）
            str_values = [str(v) if v is not None else None for v in values]
            arrays.append(pa.array(str_values, type=pa.string()))
            fields.append(pa.field(col, pa.string()))

    schema = pa.schema(fields)
    table = pa.Table.from_arrays(arrays, schema=schema)
    pq.write_table(table, str(path), compression=compression)


# ─── 核心导出逻辑 ───────────────────────────────────────────────


def export_run_id(conn, run_id: str, output_dir: Path) -> dict:
    """导出单个 run_id 的所有数据"""
    folder_name = run_id if run_id else "__global__"
    run_dir = output_dir / folder_name
    run_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "run_id": folder_name,
        "models": 0,
        "metadata": 0,
        "details": 0,
        "archived_count": 0,
    }

    # ── 1. 模型配置（JSON）──
    models = fetch_models_by_run_id(conn, run_id)
    models_file = run_dir / "models.json"
    write_json(models, models_file)
    stats["models"] = len(models)
    print(f"  [模型] {len(models)} 条 → {models_file.relative_to(output_dir)}")

    # ── 2. 请求元数据（Parquet）──
    meta_columns, meta_rows = fetch_request_metadata_by_run_id(conn, run_id)
    meta_data = [row_to_dict(meta_columns, row) for row in meta_rows]
    meta_file = run_dir / "request_metadata.parquet"
    dicts_to_parquet(meta_data, meta_file)
    stats["metadata"] = len(meta_rows)
    print(f"  [元数据] {len(meta_rows)} 条 → {meta_file.relative_to(output_dir)}")

    # ── 3. 请求详情（Parquet）──
    archive_idx = meta_columns.index("archive_location")
    unique_id_idx = meta_columns.index("unique_id")
    active_unique_ids = []
    archived_pairs = []
    for row in meta_rows:
        if row[archive_idx] is None:
            active_unique_ids.append(row[unique_id_idx])
        else:
            archived_pairs.append((row[unique_id_idx], row[archive_idx]))

    stats["archived_count"] = len(archived_pairs)

    detail_columns, detail_rows = fetch_request_details_by_unique_ids(conn, active_unique_ids)
    detail_data = [row_to_dict(detail_columns, row) for row in detail_rows]
    detail_file = run_dir / "request_details.parquet"
    dicts_to_parquet(detail_data, detail_file)
    stats["details"] = len(detail_rows)
    print(f"  [详情] {len(detail_rows)} 条活跃 → {detail_file.relative_to(output_dir)}")

    if archived_pairs:
        archive_trace = [
            {"unique_id": uid, "archive_location": loc}
            for uid, loc in archived_pairs
        ]
        trace_file = run_dir / "archived_trace.json"
        write_json(archive_trace, trace_file)
        print(f"  [归档追踪] {len(archived_pairs)} 条已归档 → {trace_file.relative_to(output_dir)}")

    return stats


def export_all(conn, output_dir: Path, run_ids: Optional[list[str]] = None) -> list[dict]:
    """执行完整导出"""
    if run_ids is None:
        run_ids = get_all_run_ids(conn)

    print(f"\n{'=' * 60}")
    print(f"数据库导出开始")
    print(f"输出目录: {output_dir}")
    print(f"待导出 run_id 数量: {len(run_ids)}")
    print(f"{'=' * 60}\n")

    all_stats = []
    for run_id in run_ids:
        display = run_id if run_id else "__global__ (全局模型)"
        print(f"▶ 导出 run_id: {display}")
        stats = export_run_id(conn, run_id, output_dir)
        all_stats.append(stats)
        print()

    summary = {
        "export_time": datetime.now(timezone.utc).isoformat(),
        "database_host": str(conn.info.host),
        "database_port": str(conn.info.port),
        "database_dbname": str(conn.info.dbname),
        "total_runs": len(all_stats),
        "runs": all_stats,
    }
    summary_file = output_dir / "export_summary.json"
    with open(summary_file, "wb") as f:
        f.write(orjson.dumps(summary, option=orjson.OPT_INDENT_2))

    print(f"{'=' * 60}")
    print(f"导出完成！摘要文件: {summary_file}")
    print(f"总 run_id 数: {len(all_stats)}")
    print(f"总模型配置: {sum(s['models'] for s in all_stats)} 条")
    print(f"总请求元数据: {sum(s['metadata'] for s in all_stats)} 条")
    print(f"总请求详情: {sum(s['details'] for s in all_stats)} 条")
    print(f"{'=' * 60}")

    return all_stats


# ─── 命令行入口 ───────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="数据库导出脚本 - 按 run_id 导出所有数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scripts/export_database.py
  python scripts/export_database.py -o /path/to/export
  python scripts/export_database.py --run-id app-001 --run-id app-002
  python scripts/export_database.py --db-url postgresql://user:pass@host:5432/db
        """
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="./export_output",
        help="导出输出目录（默认: ./export_output）"
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="数据库连接 URL（默认从 configs/config.yaml 或 DATABASE_URL 环境变量读取）"
    )
    parser.add_argument(
        "--run-id",
        type=str,
        action="append",
        default=None,
        help="指定导出的 run_id（可多次使用，默认导出所有）"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    db_url = get_database_url(args.db_url)
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"连接数据库: {db_url.split('@')[-1] if '@' in db_url else db_url}")
    try:
        conn = psycopg.connect(db_url)
    except Exception as e:
        print(f"[ERROR] 无法连接数据库: {e}")
        sys.exit(1)

    try:
        run_ids = args.run_id if args.run_id else None
        export_all(conn, output_dir, run_ids)
    except Exception as e:
        print(f"\n[ERROR] 导出过程中出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()