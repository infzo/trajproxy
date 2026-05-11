# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from vllm/logger.py

"""
vLLM logger 适配器

提供与 vllm.logger.init_logger 兼容的日志接口，
包括 debug_once / info_once / warning_once 方法。
"""
import logging
from functools import lru_cache
from types import MethodType
from typing import Optional


@lru_cache
def _print_debug_once(logger: logging.Logger, msg: str, *args) -> None:
    logger.debug(msg, *args, stacklevel=3)


@lru_cache
def _print_info_once(logger: logging.Logger, msg: str, *args) -> None:
    logger.info(msg, *args, stacklevel=3)


@lru_cache
def _print_warning_once(logger: logging.Logger, msg: str, *args) -> None:
    logger.warning(msg, *args, stacklevel=3)


def _debug_once(self, msg: str, *args) -> None:
    _print_debug_once(self, msg, *args)


def _info_once(self, msg: str, *args) -> None:
    _print_info_once(self, msg, *args)


def _warning_once(self, msg: str, *args) -> None:
    _print_warning_once(self, msg, *args)


_METHODS_TO_PATCH = {
    "debug_once": _debug_once,
    "info_once": _info_once,
    "warning_once": _warning_once,
}


def init_logger(name: str) -> logging.Logger:
    """初始化日志器

    返回的 logger 额外支持 debug_once / info_once / warning_once 方法，
    同一消息只会打印一次。

    Args:
        name: 日志器名称（通常是 __name__）

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)

    # patch *_once 方法
    for method_name, method in _METHODS_TO_PATCH.items():
        setattr(logger, method_name, MethodType(method, logger))

    return logger


__all__ = ["init_logger"]
