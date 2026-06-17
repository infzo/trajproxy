# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
导入工具函数
"""
import importlib.util
import sys
from pathlib import Path
from typing import Any


def import_from_path(module_name: str, file_path: str) -> Any:
    """从文件路径导入模块

    Args:
        module_name: 模块名称
        file_path: 文件路径

    Returns:
        导入的模块对象
    """
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module {module_name} from {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def resolve_obj_by_qualname(qualname: str) -> Any:
    """通过限定名解析对象

    Args:
        qualname: 对象的限定名（如 "module.submodule.ClassName"）

    Returns:
        解析后的对象
    """
    parts = qualname.rsplit(".", 1)
    if len(parts) == 1:
        raise ValueError(f"Invalid qualified name: {qualname}")

    module_path, obj_name = parts
    module = importlib.import_module(module_path)
    return getattr(module, obj_name)


__all__ = ["import_from_path", "resolve_obj_by_qualname"]
