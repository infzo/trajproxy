"""
Tokenizer 仓库 - 管理数据库中的 tokenizer 压缩包
"""

import os
import tarfile
import fcntl
from typing import List
from io import BytesIO

from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


class TokenizerRepository:
    """Tokenizer 数据库存储仓库

    将 tokenizer 打包为 tar.gz 存储在数据库中，
    运行时按需下载解压到本地 models 目录。
    """

    def __init__(self, db_manager: DatabaseManager):
        self._db_manager = db_manager

    async def list(self) -> List[dict]:
        """列出数据库中所有 tokenizer

        Returns:
            tokenizer 信息列表，每项包含 name, size, file_count, created_at
        """
        async with self._db_manager.pool.connection() as conn:
            rows = await conn.execute(
                "SELECT name, size, file_count, created_at "
                "FROM tokenizer_packages ORDER BY name"
            )
            return [dict(row) for row in rows.fetchall()]

    async def exists(self, name: str) -> bool:
        """检查 tokenizer 是否存在

        Args:
            name: tokenizer 名称，如 "Qwen/Qwen3.5-2B"

        Returns:
            是否存在
        """
        async with self._db_manager.pool.connection() as conn:
            row = await conn.execute(
                "SELECT 1 FROM tokenizer_packages WHERE name = %s",
                (name,)
            )
            return row.fetchone() is not None

    async def download_to_local(self, name: str, local_dir: str) -> str:
        """从数据库下载 tokenizer 并解压到本地

        Args:
            name: tokenizer 名称，如 "Qwen/Qwen3.5-2B"
            local_dir: 本地根目录（如 /app/models）

        Returns:
            解压后的本地目录绝对路径

        Raises:
            ValueError: tokenizer 不存在于数据库
        """
        target_path = os.path.join(local_dir, name)

        # 已存在则跳过
        if os.path.exists(target_path):
            logger.debug(f"tokenizer 已存在于本地: {target_path}")
            return target_path

        # 文件锁防并发下载
        lock_path = f"/tmp/tokenizer_dl_{name.replace('/', '_')}.lock"
        with open(lock_path, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                # 双重检查
                if os.path.exists(target_path):
                    return target_path

                # 从数据库读取压缩包
                async with self._db_manager.pool.connection() as conn:
                    row = await conn.execute(
                        "SELECT content FROM tokenizer_packages WHERE name = %s",
                        (name,)
                    )
                    result = row.fetchone()

                if not result:
                    raise ValueError(f"tokenizer '{name}' 不存在于数据库中")

                # 解压到目标目录
                tar_buffer = BytesIO(bytes(result["content"]))
                os.makedirs(target_path, exist_ok=True)
                with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
                    # 安全检查：防止路径穿越
                    for member in tar.getmembers():
                        if member.name.startswith('/') or '..' in member.name:
                            raise ValueError(f"不安全的压缩包路径: {member.name}")
                    tar.extractall(path=target_path)

            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

        logger.info(f"tokenizer 从数据库下载解压完成: {target_path}")
        return target_path

    async def upload_from_local(self, name: str, local_path: str) -> dict:
        """从本地目录打包上传 tokenizer 到数据库

        Args:
            name: tokenizer 名称
            local_path: 本地 tokenizer 目录路径

        Returns:
            上传信息 {"size": int, "file_count": int}

        Raises:
            ValueError: 目录不存在或为空
        """
        if not os.path.isdir(local_path):
            raise ValueError(f"目录不存在: {local_path}")

        # 打包为 tar.gz
        tar_buffer = BytesIO()
        file_count = 0
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            for root, _, filenames in os.walk(local_path):
                for filename in filenames:
                    file_full_path = os.path.join(root, filename)
                    arcname = os.path.relpath(file_full_path, local_path)
                    tar.add(file_full_path, arcname=arcname)
                    file_count += 1

        content = tar_buffer.getvalue()
        size = len(content)

        if size == 0:
            raise ValueError(f"目录为空: {local_path}")

        # 存入数据库（UPSERT）
        async with self._db_manager.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO tokenizer_packages (name, content, size, file_count) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET "
                "content = EXCLUDED.content, size = EXCLUDED.size, "
                "file_count = EXCLUDED.file_count, created_at = NOW()",
                (name, content, size, file_count)
            )

        logger.info(f"tokenizer 上传完成: {name} (size={size}, files={file_count})")
        return {"size": size, "file_count": file_count}

    async def delete(self, name: str) -> bool:
        """删除数据库中的 tokenizer

        Args:
            name: tokenizer 名称

        Returns:
            是否成功删除（不存在时返回 False）
        """
        async with self._db_manager.pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM tokenizer_packages WHERE name = %s",
                (name,)
            )
            deleted = result.rowcount > 0
            if deleted:
                logger.info(f"tokenizer 已删除: {name}")
            return deleted
