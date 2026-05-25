"""
存储抽象层 - 统一本地文件系统、S3 和 CSB 存储接口

根据配置自动选择存储后端：
- s3.app_token 非空 → CSB 网关模式（华为云 CSB 原生 REST API）
- s3.bucket 非空 → 标准 S3 模式（boto3）
- 否则 → 本地模式，archive_location 为文件名
"""

import logging
import os
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

    def validate(self) -> None:
        """验证存储可用性：上传一个探测文件再删除，失败则抛出异常"""
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

    def validate(self) -> None:
        probe_key = ".probe/archiver_startup_check"
        probe_path = self.storage_path / ".probe_tmp"
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text("archiver startup probe")
        try:
            self.upload(probe_path, probe_key)
            # 清理探测文件
            dest = self.storage_path / probe_key
            if dest.exists():
                dest.unlink()
        finally:
            probe_path.unlink(missing_ok=True)
        logger.info("LocalStorage 验证通过")


def create_storage(config: dict) -> Storage:
    """根据配置创建存储实例

    Args:
        config: archive 配置字典，包含 s3 子配置和 storage_path

    Returns:
        Storage 实例（CSBStorage / S3Storage / LocalStorage）

    存储后端优先级:
      1. s3.app_token / CSB_APP_TOKEN 非空 → CSB 网关模式（原生 REST API）
      2. s3.bucket 非空 → 标准 S3 模式（boto3）
      3. 否则 → 本地文件系统模式
    """
    s3_config = config.get("s3")

    if s3_config and s3_config.get("bucket"):
        # app_token: YAML 显式配置优先，其次环境变量 CSB_APP_TOKEN
        app_token = s3_config.get("app_token") or os.environ.get("CSB_APP_TOKEN")

        if app_token:
            # CSB 网关模式：使用原生 REST API
            from traj_archiver.csb_storage import CSBStorage

            return CSBStorage(
                bucket=s3_config["bucket"],
                prefix=s3_config.get("prefix", ""),
                endpoint_url=s3_config.get("endpoint_url", ""),
                app_token=app_token,
                vendor=s3_config.get("vendor", "HEC"),
                region=s3_config.get("region", ""),
            )

        # 标准 S3 模式：使用 boto3
        from traj_archiver.s3_storage import S3Storage

        return S3Storage(
            bucket=s3_config["bucket"],
            prefix=s3_config.get("prefix", ""),
            endpoint_url=s3_config.get("endpoint_url"),
            access_key=s3_config.get("access_key"),
            secret_key=s3_config.get("secret_key"),
            session_token=s3_config.get("session_token"),
            region=s3_config.get("region"),
            verify_ssl=s3_config.get("verify_ssl", True),
        )

    # 本地模式
    storage_path = config.get("storage_path", "/data/archives")
    return LocalStorage(storage_path=storage_path)
