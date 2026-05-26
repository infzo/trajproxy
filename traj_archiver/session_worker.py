"""
Session 粒度归档 Worker (Ray Actor)

独立进程，每次 archive() 创建新 DB 连接，处理单个 session 的完整归档流程:
读 DB → 写 .jsonl.gz → 上传存储
"""

import gzip
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
import ray

from traj_archiver.storage import create_storage

# Ray 子进程不继承主进程的 basicConfig，需要独立配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# 每批 FETCH 行数
_BATCH_SIZE = 500

# 归档查询列（与 SELECT 顺序一致）
_ARCHIVE_COLUMNS = [
    "unique_id", "tokenizer_path", "messages",
    "raw_request", "raw_response", "text_request", "text_response",
    "prompt_text", "token_ids", "token_request", "token_response",
    "response_text", "response_ids",
    "full_conversation_text", "full_conversation_token_ids",
    "error_traceback", "created_at",
]

# session_id 为 NULL 时使用的占位名
_NULL_SESSION_DIR = "_null_session"


def _safe_path(segment: str) -> str:
    return segment.replace(",", "_").replace("/", "_")


@ray.remote
class SessionArchiveWorker:
    """Session 粒度归档 Worker

    Ray Actor，独立进程运行。storage 实例常驻复用，DB 连接每次新建。
    """

    def __init__(
        self,
        worker_id: int,
        db_url: str,
        storage_config: dict,
        temp_root: str,
        compress: bool = True,
    ):
        self.worker_id = worker_id
        self.db_url = db_url
        self.storage = create_storage(storage_config)
        self.temp_root = Path(temp_root)
        self.compress = compress
        self.temp_root.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"SessionArchiveWorker-{worker_id} 初始化: "
            f"temp={temp_root}, compress={compress}"
        )

    def archive(self, run_id: str, session_id: Optional[str]) -> Dict[str, Any]:
        """归档单个 session：读 DB → 写文件 → 上传

        返回: {"records": int, "file": str}
        """
        display = session_id or _NULL_SESSION_DIR
        safe_run = _safe_path(run_id)
        safe_session = _safe_path(display)
        suffix = ".jsonl.gz" if self.compress else ".jsonl"
        logger.info(f"Worker-{self.worker_id}: 开始归档 session '{display}' (run={run_id})")

        run_dir = self.temp_root / safe_run
        run_dir.mkdir(parents=True, exist_ok=True)
        file_path = run_dir / f"{safe_session}{suffix}"

        try:
            record_count = self._export_session(
                run_id, session_id, file_path, safe_run, safe_session, suffix,
            )

            if record_count == 0:
                file_path.unlink(missing_ok=True)
                return {"records": 0, "file": None}

            # 上传
            key = f"{safe_run}/{safe_session}{suffix}"
            loc = self.storage.upload(file_path, key)
            logger.info(
                f"Worker-{self.worker_id}: "
                f"session '{display}' 上传完成 ({record_count} 条)"
            )
            return {"records": record_count, "file": loc}

        except Exception:
            # 上传失败时保留文件以便排查，但不阻碍其他 Worker
            logger.exception(
                f"Worker-{self.worker_id}: session '{display}' 归档失败"
            )
            raise

        finally:
            file_path.unlink(missing_ok=True)
            # 空目录也清理
            if run_dir.exists():
                try:
                    run_dir.rmdir()
                except OSError:
                    pass

    def _export_session(
        self,
        run_id: str,
        session_id: Optional[str],
        file_path: Path,
        safe_run: str,
        safe_session: str,
        suffix: str,
    ) -> int:
        """从 DB 读取 session 数据并写入文件，返回记录数"""
        cursor_name = f"arch_{safe_run}_{safe_session}"

        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                # NULL session_id 需要 IS NULL 比较
                if session_id is None:
                    cur.execute(f"""
                        DECLARE "{cursor_name}" CURSOR FOR
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
                        WHERE m.run_id = %s AND m.session_id IS NULL
                        ORDER BY d.created_at
                    """, (run_id,))
                else:
                    cur.execute(f"""
                        DECLARE "{cursor_name}" CURSOR FOR
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
                        WHERE m.run_id = %s AND m.session_id = %s
                        ORDER BY d.created_at
                    """, (run_id, session_id))

            fh = (
                gzip.open(file_path, "wt", encoding="utf-8")
                if self.compress
                else open(file_path, "wt", encoding="utf-8")
            )
            record_count = 0

            try:
                while True:
                    with conn.cursor() as cur:
                        cur.execute(f'FETCH {_BATCH_SIZE} FROM "{cursor_name}"')
                        batch = cur.fetchall()
                    if not batch:
                        break

                    for row in batch:
                        rec = dict(zip(_ARCHIVE_COLUMNS, row))
                        for k, v in rec.items():
                            if isinstance(v, datetime):
                                rec[k] = v.isoformat()
                        fh.write(json.dumps(rec, default=str) + "\n")
                        record_count += 1

            finally:
                fh.close()

            with conn.cursor() as cur:
                cur.execute(f'CLOSE "{cursor_name}"')
            conn.commit()

        return record_count
