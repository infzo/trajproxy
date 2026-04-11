#!/usr/bin/env python3
"""
Tokenizer 下载脚本

从 Hugging Face 下载指定模型的 tokenizer（不包括模型权重）。
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Set

try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("错误: huggingface-hub 未安装。请运行: pip install huggingface-hub>=0.20.0")
    sys.exit(1)


# 需要排除的文件扩展名（模型权重文件）
WEIGHT_EXCLUDE_PATTERNS = [
    "*.bin",
    "*.safetensors",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.onnx",
    "*.gguf",
    "*.marlin",
]


def download_tokenizer(
    model_name: str,
    output_dir: str,
    allow_patterns: List[str] = None,
    ignore_patterns: List[str] = None
) -> str:
    """下载指定模型的 tokenizer

    Args:
        model_name: Hugging Face 模型名称（如：QWen/QWen-2.5-7B）
        output_dir: 输出目录
        allow_patterns: 允许下载的文件模式
        ignore_patterns: 忽略的文件模式

    Returns:
        下载后的模型目录路径
    """
    print(f"正在下载 tokenizer: {model_name}")
    print(f"输出目录: {output_dir}")

    # 默认忽略权重文件
    if ignore_patterns is None:
        ignore_patterns = WEIGHT_EXCLUDE_PATTERNS.copy()

    try:
        download_path = snapshot_download(
            repo_id=model_name,
            cache_dir=None,  # 使用默认缓存目录
            local_dir=output_dir,
            local_dir_use_symlinks=False,
            allow_patterns=allow_patterns,
            ignore_patterns=ignore_patterns,
            resume_download=True,
        )
        print(f"下载成功: {download_path}")
        return download_path
    except Exception as e:
        print(f"下载失败: {e}")
        raise


def validate_tokenizer(model_path: str) -> bool:
    """验证下载的 tokenizer 是否有效

    Args:
        model_path: 模型目录路径

    Returns:
        是否有效
    """
    # 检查必要的 tokenizer 文件
    required_files: Set[str] = set()
    optional_files: Set[str] = set()

    # 遍历目录中的文件
    if not os.path.exists(model_path):
        return False

    for file in os.listdir(model_path):
        if file.endswith(".json"):
            if "tokenizer" in file:
                required_files.add(file)
        elif file.endswith(".txt"):
            optional_files.add(file)

    # 至少需要一个 tokenizer 相关的 json 文件
    has_tokenizer_config = "tokenizer_config.json" in required_files
    has_tokenizer_json = "tokenizer.json" in required_files

    if not has_tokenizer_config and not has_tokenizer_json:
        return False

    return True


def list_tokenizers(models_dir: str) -> List[str]:
    """列出已下载的 tokenizer

    Args:
        models_dir: models 目录路径

    Returns:
        模型名称列表（相对于 models_dir 的路径）
    """
    if not os.path.exists(models_dir):
        print(f"目录不存在: {models_dir}")
        return []

    models = []

    # 递归查找包含 tokenizer 文件的目录
    def find_tokenizers(current_dir: str, relative_path: str = ""):
        for item in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item)
            item_relative = os.path.join(relative_path, item) if relative_path else item

            if os.path.isdir(item_path):
                # 检查是否是有效的 tokenizer 目录
                if validate_tokenizer(item_path):
                    models.append(item_relative)
                else:
                    # 递归查找子目录
                    find_tokenizers(item_path, item_relative)

    find_tokenizers(models_dir)
    return models


def get_default_output_dir() -> str:
    """获取默认输出目录

    Returns:
        默认的 models 目录路径
    """
    # 优先使用当前目录下的 models
    current_models = os.path.join(os.getcwd(), "models")
    if os.path.exists(current_models):
        return current_models

    # 检查 traj_proxy/models
    traj_proxy_models = os.path.join(os.getcwd(), "traj_proxy", "models")
    if os.path.exists(traj_proxy_models):
        return traj_proxy_models

    # 默认使用当前目录下的 models
    return current_models


def main():
    default_output_dir = get_default_output_dir()

    parser = argparse.ArgumentParser(
        description="从 Hugging Face 下载模型的 tokenizer（不包括权重）"
    )
    parser.add_argument(
        "--model",
        action="append",
        help="要下载的模型名称（可重复指定多个），例如：QWen/QWen-2.5-7B"
    )
    parser.add_argument(
        "--output-dir",
        default=default_output_dir,
        help=f"输出目录（默认：{default_output_dir}）"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出已下载的 tokenizer"
    )
    parser.add_argument(
        "--allow-patterns",
        action="append",
        help="允许下载的文件模式（可重复指定）"
    )
    parser.add_argument(
        "--ignore-patterns",
        action="append",
        help="忽略的文件模式（可重复指定）"
    )

    args = parser.parse_args()

    # 列出已下载的模型
    if args.list:
        models = list_tokenizers(args.output_dir)
        if models:
            print(f"\n已下载的 tokenizer（共 {len(models)} 个）：")
            for i, model in enumerate(models, 1):
                print(f"  {i}. {model}")
        else:
            print("\n未找到已下载的 tokenizer")
        return

    # 下载模型
    if not args.model:
        parser.print_help()
        print("\n错误：请指定要下载的模型（使用 --model 参数）或使用 --list 查看已下载的模型")
        sys.exit(1)

    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)

    # 合并忽略模式
    ignore_patterns = WEIGHT_EXCLUDE_PATTERNS.copy()
    if args.ignore_patterns:
        ignore_patterns.extend(args.ignore_patterns)

    # 下载每个模型
    for model_name in args.model:
        model_output_dir = os.path.join(args.output_dir, model_name)
        try:
            download_tokenizer(
                model_name=model_name,
                output_dir=model_output_dir,
                allow_patterns=args.allow_patterns,
                ignore_patterns=ignore_patterns
            )
            print(f"\n✓ {model_name} 下载完成\n")
        except Exception as e:
            print(f"\n✗ {model_name} 下载失败: {e}\n")


if __name__ == "__main__":
    main()
