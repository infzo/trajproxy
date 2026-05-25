"""
存储抽象层 - 统一本地文件系统和 S3 存储接口

根据配置自动选择存储后端：
- 配置中有 s3.bucket → S3 模式，archive_location 为 s3:// URI
- 否则 → 本地模式，archive_location 为文件名
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Storage:
    """存储操作统一接口"""

    def upload(self, local_path: Path, key: str) -> str:
        """上传文件，返回 archive_location 标识"""
        raise NotImplementedError

    def download(self, key: str, local_path: Path) -> Path:
        """下载文件到本地路径"""
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        """检查文件是否存在"""
        raise NotImplementedError


class LocalStorage(Storage):
    """本地文件系统存储

    文件直接保存到 storage_path 目录，archive_location 为文件名。
    """

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage 初始化: path={self.storage_path}")

    def upload(self, local_path: Path, key: str) -> str:
        dest = self.storage_path / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(local_path), str(dest))
        logger.info(f"已保存: {local_path.name} → {dest}")
        return key

    def download(self, key: str, local_path: Path) -> Path:
        src = self.storage_path / key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(local_path))
        logger.info(f"已下载: {src} → {local_path}")
        return local_path

    def exists(self, key: str) -> bool:
        return (self.storage_path / key).exists()


def create_storage(config: dict) -> Storage:
    """根据配置创建存储实例

    Args:
        config: archive 配置字典，包含 s3 子配置和 storage_path

    Returns:
        Storage 实例（LocalStorage 或 S3Storage）
    """
    s3_config = config.get("s3")

    # S3 模式：s3 配置非空且有 bucket
    if s3_config and s3_config.get("bucket"):
        from traj_archiver.s3_storage import S3Storage

        return S3Storage(
            bucket=s3_config["bucket"],
            prefix=s3_config.get("prefix", ""),
            endpoint_url=s3_config.get("endpoint_url"),
        )

    # 本地模式
    storage_path = config.get("storage_path", "/data/archives")
    return LocalStorage(storage_path=storage_path)
