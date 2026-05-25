"""
存储后端工厂 - 根据配置创建存储实例

三种模式：
  1. app_token 非空 → CSB 网关（原生 REST API）
  2. bucket 非空   → 标准 S3（boto3）
  3. 否则          → 本地文件系统
"""

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


# ============================================================
# 本地文件系统存储
# ============================================================


class LocalStorage:
    """本地文件系统存储，archive_location 为相对路径 key"""

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
            dest = self.storage_path / probe_key
            if dest.exists():
                dest.unlink()
        finally:
            probe_path.unlink(missing_ok=True)
        logger.info("LocalStorage 验证通过")


# ============================================================
# 工厂函数
# ============================================================


def create_storage(config: dict):
    """根据配置创建存储实例

    优先级:
      1. s3.app_token / CSB_APP_TOKEN → CSB 网关
      2. s3.bucket → 标准 S3
      3. 无 s3 配置 → 本地文件系统
    """
    s3_config = config.get("s3")

    if s3_config and s3_config.get("bucket"):
        app_token = s3_config.get("app_token") or os.environ.get("CSB_APP_TOKEN")

        if app_token:
            from traj_archiver.csb_storage import CSBStorage
            return CSBStorage(
                bucket=s3_config["bucket"],
                prefix=s3_config.get("prefix", ""),
                endpoint_url=s3_config.get("endpoint_url", ""),
                app_token=app_token,
                vendor=s3_config.get("vendor", "HEC"),
                region=s3_config.get("region", ""),
            )

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

    storage_path = config.get("storage_path", "/data/archives")
    return LocalStorage(storage_path=storage_path)
