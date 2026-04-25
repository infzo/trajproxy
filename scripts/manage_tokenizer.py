#!/usr/bin/env python3
"""
Tokenizer 管理工具

用于管理数据库中的 tokenizer 压缩包。

命令:
    upload       上传本地 tokenizer 到数据库
    list         列出数据库中的所有 tokenizer
    delete       从数据库删除 tokenizer
    upload-all   批量上传本地 models 目录下的所有 tokenizer
    download     从数据库下载 tokenizer 到本地（测试用）

示例:
    # 上传单个 tokenizer
    python scripts/manage_tokenizer.py upload \\
        --name Qwen/Qwen3.5-2B \\
        --path ./models/Qwen/Qwen3.5-2B

    # 列出所有 tokenizer
    python scripts/manage_tokenizer.py list

    # 删除 tokenizer
    python scripts/manage_tokenizer.py delete --name Qwen/Qwen3.5-2B

    # 批量上传
    python scripts/manage_tokenizer.py upload-all --models-dir ./models

    # 下载测试
    python scripts/manage_tokenizer.py download \\
        --name Qwen/Qwen3.5-2B \\
        --output ./test_output
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from traj_proxy.store.database_manager import DatabaseManager
from traj_proxy.store.tokenizer_repository import TokenizerRepository
from traj_proxy.utils.config import get_database_config


def get_db_url() -> str:
    """获取数据库连接 URL"""
    db_config = get_database_config()
    return db_config.get("url", os.getenv("DATABASE_URL", ""))


def create_repo(db_url: str) -> tuple[DatabaseManager, TokenizerRepository]:
    """创建数据库管理器和 TokenizerRepository"""
    db_manager = DatabaseManager(db_url)
    asyncio.run(db_manager.initialize())
    repo = TokenizerRepository(db_manager)
    return db_manager, repo


def cmd_upload(args):
    """上传 tokenizer 到数据库"""
    if not args.name:
        print("错误: 请指定 --name")
        return 1
    if not args.path:
        print("错误: 请指定 --path")
        return 1
    if not os.path.isdir(args.path):
        print(f"错误: 目录不存在: {args.path}")
        return 1

    db_url = args.db_url or get_db_url()
    if not db_url:
        print("错误: 请设置 DATABASE_URL 环境变量或通过 --db-url 指定")
        return 1

    db_manager, repo = create_repo(db_url)

    try:
        result = asyncio.run(repo.upload_from_local(args.name, args.path))
        print(f"上传成功: {args.name}")
        print(f"  大小: {result['size']} 字节")
        print(f"  文件数: {result['file_count']}")
        return 0
    except Exception as e:
        print(f"上传失败: {e}")
        return 1
    finally:
        asyncio.run(db_manager.close())


def cmd_list(args):
    """列出数据库中的所有 tokenizer"""
    db_url = args.db_url or get_db_url()
    if not db_url:
        print("错误: 请设置 DATABASE_URL 环境变量或通过 --db-url 指定")
        return 1

    db_manager, repo = create_repo(db_url)

    try:
        tokenizers = asyncio.run(repo.list())
        if not tokenizers:
            print("数据库中暂无 tokenizer")
            return 0

        print(f"数据库中的 tokenizer (共 {len(tokenizers)} 个):")
        print("-" * 80)
        for t in tokenizers:
            size_kb = t['size'] / 1024
            print(f"  {t['name']}")
            print(f"    大小: {size_kb:.1f} KB, 文件数: {t['file_count'] or 'N/A'}")
            print(f"    创建时间: {t['created_at']}")
        return 0
    except Exception as e:
        print(f"查询失败: {e}")
        return 1
    finally:
        asyncio.run(db_manager.close())


def cmd_delete(args):
    """从数据库删除 tokenizer"""
    if not args.name:
        print("错误: 请指定 --name")
        return 1

    db_url = args.db_url or get_db_url()
    if not db_url:
        print("错误: 请设置 DATABASE_URL 环境变量或通过 --db-url 指定")
        return 1

    db_manager, repo = create_repo(db_url)

    try:
        deleted = asyncio.run(repo.delete(args.name))
        if deleted:
            print(f"删除成功: {args.name}")
            return 0
        else:
            print(f"tokenizer 不存在: {args.name}")
            return 1
    except Exception as e:
        print(f"删除失败: {e}")
        return 1
    finally:
        asyncio.run(db_manager.close())


def cmd_upload_all(args):
    """批量上传本地 models 目录下的所有 tokenizer"""
    models_dir = args.models_dir or os.path.join(project_root, "models")
    if not os.path.isdir(models_dir):
        print(f"错误: 目录不存在: {models_dir}")
        return 1

    db_url = args.db_url or get_db_url()
    if not db_url:
        print("错误: 请设置 DATABASE_URL 环境变量或通过 --db-url 指定")
        return 1

    # 查找所有包含 tokenizer_config.json 的目录
    tokenizer_dirs = []
    for root, dirs, files in os.walk(models_dir):
        if "tokenizer_config.json" in files:
            rel_path = os.path.relpath(root, models_dir)
            tokenizer_dirs.append((rel_path, root))

    if not tokenizer_dirs:
        print(f"未在 {models_dir} 中找到 tokenizer")
        return 0

    print(f"找到 {len(tokenizer_dirs)} 个 tokenizer:")
    for name, path in tokenizer_dirs:
        print(f"  - {name}")

    if not args.yes:
        confirm = input("确认上传? [y/N] ")
        if confirm.lower() != 'y':
            print("已取消")
            return 0

    db_manager, repo = create_repo(db_url)

    try:
        success_count = 0
        for name, path in tokenizer_dirs:
            try:
                result = asyncio.run(repo.upload_from_local(name, path))
                print(f"  ✓ {name} ({result['size']} 字节, {result['file_count']} 文件)")
                success_count += 1
            except Exception as e:
                print(f"  ✗ {name}: {e}")

        print(f"\n完成: {success_count}/{len(tokenizer_dirs)} 成功")
        return 0 if success_count == len(tokenizer_dirs) else 1
    finally:
        asyncio.run(db_manager.close())


def cmd_download(args):
    """从数据库下载 tokenizer 到本地（测试用）"""
    if not args.name:
        print("错误: 请指定 --name")
        return 1
    if not args.output:
        print("错误: 请指定 --output")
        return 1

    db_url = args.db_url or get_db_url()
    if not db_url:
        print("错误: 请设置 DATABASE_URL 环境变量或通过 --db-url 指定")
        return 1

    db_manager, repo = create_repo(db_url)

    try:
        os.makedirs(args.output, exist_ok=True)
        result_path = asyncio.run(repo.download_to_local(args.name, args.output))
        print(f"下载成功: {result_path}")
        return 0
    except Exception as e:
        print(f"下载失败: {e}")
        return 1
    finally:
        asyncio.run(db_manager.close())


def main():
    parser = argparse.ArgumentParser(
        description="Tokenizer 管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--db-url", help="数据库连接 URL（默认从 DATABASE_URL 环境变量读取）")

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # upload 命令
    upload_parser = subparsers.add_parser("upload", help="上传 tokenizer 到数据库")
    upload_parser.add_argument("--name", required=True, help="tokenizer 名称，如 Qwen/Qwen3.5-2B")
    upload_parser.add_argument("--path", required=True, help="本地 tokenizer 目录路径")

    # list 命令
    subparsers.add_parser("list", help="列出数据库中的所有 tokenizer")

    # delete 命令
    delete_parser = subparsers.add_parser("delete", help="从数据库删除 tokenizer")
    delete_parser.add_argument("--name", required=True, help="tokenizer 名称")

    # upload-all 命令
    upload_all_parser = subparsers.add_parser("upload-all", help="批量上传本地 models 目录下的所有 tokenizer")
    upload_all_parser.add_argument("--models-dir", help="本地 models 目录路径")
    upload_all_parser.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    # download 命令
    download_parser = subparsers.add_parser("download", help="从数据库下载 tokenizer 到本地（测试用）")
    download_parser.add_argument("--name", required=True, help="tokenizer 名称")
    download_parser.add_argument("--output", required=True, help="输出目录")

    args = parser.parse_args()

    if args.command == "upload":
        return cmd_upload(args)
    elif args.command == "list":
        return cmd_list(args)
    elif args.command == "delete":
        return cmd_delete(args)
    elif args.command == "upload-all":
        return cmd_upload_all(args)
    elif args.command == "download":
        return cmd_download(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
