# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

"""
集合工具函数
"""
from typing import Any, TypeVar, Sequence, TypeGuard

T = TypeVar('T')


def is_list_of(lst: Sequence[Any], typ: type[T]) -> TypeGuard[list[T]]:
    """检查序列是否全部为指定类型

    Args:
        lst: 要检查的序列
        typ: 目标类型

    Returns:
        如果所有元素都是 typ 类型，返回 True
    """
    return all(isinstance(x, typ) for x in lst)


__all__ = ["is_list_of"]
