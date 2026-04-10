"""
参数校验工具函数

提供 session_id、run_id、model_name 等参数的格式校验。
"""

import re
from typing import Optional, Tuple


# 默认 run_id 常量
DEFAULT_RUN_ID = "DEFAULT"


def normalize_run_id(run_id: Optional[str]) -> str:
    """
    将空 run_id 标准化为 DEFAULT

    Args:
        run_id: 原始 run_id 值

    Returns:
        标准化后的 run_id（空值返回 DEFAULT，非空返回原值）
    """
    return run_id or DEFAULT_RUN_ID


def validate_session_id(session_id: Optional[str]) -> Tuple[bool, str]:
    """
    校验 session_id 格式

    session_id 为任意非空字符串，用于轨迹分组查询。
    不定义分割格式，完全由客户端自定义。

    Args:
        session_id: 会话ID

    Returns:
        (是否有效, 错误信息)
    """
    if session_id is None or session_id == "":
        return True, ""  # 允许为空

    # 任意非空字符串都合法
    return True, ""


def validate_run_id(run_id: str) -> Tuple[bool, str]:
    """
    校验 run_id 格式

    Args:
        run_id: 运行ID

    Returns:
        (是否有效, 错误信息)
    """
    if ',' in run_id:
        return False, "run_id 不能包含逗号"

    if len(run_id) > 50:
        return False, f"run_id 长度不能超过 50 个字符，当前 {len(run_id)} 个字符"

    return True, ""


def validate_model_name(model_name: str) -> Tuple[bool, str]:
    """
    校验 model_name 格式

    Args:
        model_name: 模型名称

    Returns:
        (是否有效, 错误信息)
    """
    if not model_name or not model_name.strip():
        return False, "model_name 不能为空"

    return True, ""


def validate_model_for_inference(model: str) -> Tuple[bool, str, str]:
    """
    校验推理请求中的 model 参数格式

    支持两种格式：
    - model_name (无逗号)
    - model_name,run_id (有逗号)

    Args:
        model: 模型参数

    Returns:
        (是否有效, 错误信息, 实际model_name)
    """
    if not model or not model.strip():
        return False, "model 不能为空", ""

    if ',' in model:
        parts = model.split(',', 1)
        actual_model = parts[0].strip()
        if not actual_model:
            return False, "model 格式无效，逗号前不能为空", ""
        return True, "", actual_model

    return True, "", model.strip()
