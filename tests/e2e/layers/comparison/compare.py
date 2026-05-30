#!/usr/bin/env python3
"""
trajproxy vs vLLM 响应对比工具

用法:
    python3 compare.py --mode nonstream --vllm vllm.json --proxy proxy.json --label "C300"
    python3 compare.py --mode stream --vllm vllm_sse.txt --proxy proxy_sse.txt --label "C301"

对比规则:
    - 跳过运行时动态字段: id, created
    - 非流式: 逐字段递归对比 choices[].message, usage, object, model, finish_reason 等
    - 流式: 解析 SSE chunks, 对比每个 chunk 的结构, 合并后对比完整内容
"""

import argparse
import json
import sys
import re
from typing import Any


# ========================================
# 需要跳过的字段（运行时动态值）
# ========================================
SKIP_FIELDS = {"id", "created"}

# 流式 chunk 中额外跳过的字段
STREAM_SKIP_FIELDS = SKIP_FIELDS | {"seed"}


# ========================================
# 通用递归对比引擎
# ========================================

def compare_recursive(
    vllm_val: Any,
    proxy_val: Any,
    path: str,
    errors: list,
    infos: list,
    *,
    skip_fields: set = SKIP_FIELDS,
    value_eq: bool = True,
    allow_optional_missing: bool = False,
) -> None:
    """
    递归对比两个值的一致性

    Args:
        vllm_val: vLLM 响应值
        proxy_val: trajproxy 响应值
        path: 当前字段路径（用于日志）
        errors: 错误列表
        infos: 信息列表
        skip_fields: 需跳过的字段名集合
        value_eq: 标量字段是否要求值完全相等
        allow_optional_missing: 是否允许 proxy 侧缺失可选字段
    """
    # 类型一致性检查
    vllm_type = type(vllm_val).__name__
    proxy_type = type(proxy_val).__name__

    if vllm_type != proxy_type:
        # 允许 int/float 互转（vLLM usage 字段可能类型不同）
        if vllm_type in ("int", "float") and proxy_type in ("int", "float"):
            if value_eq and vllm_val != proxy_val:
                # 允许微小浮点差异
                if isinstance(vllm_val, float) or isinstance(proxy_val, float):
                    if abs(float(vllm_val) - float(proxy_val)) < 1e-6:
                        infos.append(f"  {path}: 值一致 (数值类型不同但值等价: vllm={vllm_val!r}, proxy={proxy_val!r})")
                        return
                errors.append(f"{path}: 值不一致 (vllm={vllm_val!r}, proxy={proxy_val!r})")
            else:
                infos.append(f"  {path}: 类型兼容 (vllm={vllm_type}, proxy={proxy_type}), 值: {vllm_val!r} vs {proxy_val!r}")
            return
        errors.append(f"{path}: 类型不一致 (vllm={vllm_type}, proxy={proxy_type})")
        return

    # 字典: 递归检查每个子字段
    if isinstance(vllm_val, dict):
        # 检查字段集合一致性（排除跳过字段）
        vllm_keys = set(vllm_val.keys()) - skip_fields
        proxy_keys = set(proxy_val.keys()) - skip_fields

        missing_in_proxy = vllm_keys - proxy_keys
        extra_in_proxy = proxy_keys - vllm_keys

        if missing_in_proxy and not allow_optional_missing:
            for key in sorted(missing_in_proxy):
                errors.append(f"{path}.{key}: proxy 缺失字段 (vllm 有, proxy 无)")
        elif missing_in_proxy:
            for key in sorted(missing_in_proxy):
                infos.append(f"  {path}.{key}: proxy 缺失可选字段 (允许)")

        if extra_in_proxy:
            for key in sorted(extra_in_proxy):
                infos.append(f"  {path}.{key}: proxy 有额外字段 (vllm 无)")

        # 递归检查共有字段
        common_keys = sorted(vllm_keys & proxy_keys)
        for key in common_keys:
            child_path = f"{path}.{key}"
            compare_recursive(
                vllm_val.get(key),
                proxy_val.get(key),
                child_path,
                errors,
                infos,
                skip_fields=skip_fields,
                value_eq=value_eq,
                allow_optional_missing=allow_optional_missing,
            )

    # 列表: 检查长度，逐元素递归比较
    elif isinstance(vllm_val, list):
        if len(vllm_val) != len(proxy_val):
            errors.append(f"{path}: 长度不一致 (vllm={len(vllm_val)}, proxy={len(proxy_val)})")
            return
        for i, (v_item, p_item) in enumerate(zip(vllm_val, proxy_val)):
            child_path = f"{path}[{i}]"
            compare_recursive(
                v_item, p_item, child_path, errors, infos,
                skip_fields=skip_fields,
                value_eq=value_eq,
                allow_optional_missing=allow_optional_missing,
            )

    # 标量: 可选检查值相等
    elif value_eq:
        if vllm_val != proxy_val:
            # 对于字符串，允许微小空白差异
            if isinstance(vllm_val, str) and isinstance(proxy_val, str):
                if vllm_val.strip() == proxy_val.strip():
                    infos.append(f"  {path}: 值一致（忽略空白差异） (vllm='{vllm_val}', proxy='{proxy_val}')")
                    return
            errors.append(f"{path}: 值不一致 (vllm={vllm_val!r}, proxy={proxy_val!r})")
        else:
            infos.append(f"  {path}: 值一致 ({vllm_val!r})")
    else:
        infos.append(f"  {path}: 值存在 (vllm={vllm_val!r}, proxy={proxy_val!r})")


# ========================================
# 非流式响应对比
# ========================================

def compare_nonstream(vllm_data: dict, proxy_data: dict, label: str) -> tuple:
    """对比非流式 JSON 响应"""
    errors = []
    infos = []

    infos.append(f"【非流式对比: {label}】")

    # 顶层结构字段
    compare_recursive(
        vllm_data.get("object"),
        proxy_data.get("object"),
        "object",
        errors, infos, value_eq=True,
    )

    compare_recursive(
        vllm_data.get("model"),
        proxy_data.get("model"),
        "model",
        errors, infos, value_eq=True,
    )

    # choices 逐项对比
    vllm_choices = vllm_data.get("choices", [])
    proxy_choices = proxy_data.get("choices", [])

    if len(vllm_choices) != len(proxy_choices):
        errors.append(f"choices 镗度不一致 (vllm={len(vllm_choices)}, proxy={len(proxy_choices)})")
    else:
        infos.append(f"  choices 镗度一致 ({len(vllm_choices)})")

        for i, (v_ch, p_ch) in enumerate(zip(vllm_choices, proxy_choices)):
            ch_path = f"choices[{i}]"

            # index
            compare_recursive(v_ch.get("index"), p_ch.get("index"), f"{ch_path}.index", errors, infos)

            # finish_reason
            compare_recursive(v_ch.get("finish_reason"), p_ch.get("finish_reason"), f"{ch_path}.finish_reason", errors, infos)

            # message — 核心对比
            v_msg = v_ch.get("message", {})
            p_msg = p_ch.get("message", {})

            msg_path = f"{ch_path}.message"

            # role
            compare_recursive(v_msg.get("role"), p_msg.get("role"), f"{msg_path}.role", errors, infos)

            # content — 值必须一致
            compare_recursive(v_msg.get("content"), p_msg.get("content"), f"{msg_path}.content", errors, infos)

            # reasoning — 逐字符对比（核心字段）
            v_reasoning = v_msg.get("reasoning_content") or v_msg.get("reasoning")
            p_reasoning = p_msg.get("reasoning_content") or p_msg.get("reasoning")
            # vLLM 使用 reasoning_content, trajproxy 可能用 reasoning
            # 需要兼容两种字段名
            v_reasoning_key = "reasoning_content" if "reasoning_content" in v_msg else ("reasoning" if "reasoning" in v_msg else None)
            p_reasoning_key = "reasoning_content" if "reasoning_content" in p_msg else ("reasoning" if "reasoning" in p_msg else None)

            if v_reasoning_key and p_reasoning_key:
                compare_recursive(v_msg.get(v_reasoning_key), p_msg.get(p_reasoning_key),
                                  f"{msg_path}.reasoning", errors, infos)
                # 检查字段名是否一致
                if v_reasoning_key != p_reasoning_key:
                    infos.append(f"  {msg_path}.reasoning 字段名不同: vllm 用 '{v_reasoning_key}', proxy 用 '{p_reasoning_key}'")
            elif v_reasoning_key and not p_reasoning_key:
                errors.append(f"{msg_path}.reasoning: vllm 有推理内容, proxy 缺失")
            elif not v_reasoning_key and p_reasoning_key:
                errors.append(f"{msg_path}.reasoning: proxy 有推理内容, vllm 缺失")

            # tool_calls — 逐项对比
            v_tool_calls = v_msg.get("tool_calls")
            p_tool_calls = p_msg.get("tool_calls")

            if v_tool_calls is None and p_tool_calls is None:
                infos.append(f"  {msg_path}.tool_calls: 两者均无")
            elif v_tool_calls is not None and p_tool_calls is not None:
                compare_recursive(v_tool_calls, p_tool_calls, f"{msg_path}.tool_calls", errors, infos)
            elif v_tool_calls is not None:
                errors.append(f"{msg_path}.tool_calls: vllm 有 tool_calls, proxy 缺失")
            else:
                errors.append(f"{msg_path}.tool_calls: proxy 有 tool_calls, vllm 缺失")

    # usage 对比
    v_usage = vllm_data.get("usage", {})
    p_usage = proxy_data.get("usage", {})

    compare_recursive(v_usage, p_usage, "usage", errors, infos)

    return errors, infos


# ========================================
# 流式响应对比
# ========================================

def parse_sse_chunks(raw_text: str) -> list:
    """解析 SSE 响应文本，返回 data chunk 列表"""
    chunks = []
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data_str = line[len("data:"):].strip()
        if data_str == "[DONE]":
            continue
        try:
            chunk = json.loads(data_str)
            chunks.append(chunk)
        except json.JSONDecodeError:
            # 跳过无效行
            continue
    return chunks


def reconstruct_from_chunks(chunks: list) -> dict:
    """
    从 SSE chunks 重建完整响应（模拟非流式响应结构）

    用于对比流式响应的完整内容
    """
    if not chunks:
        return {}

    # 取第一个 chunk 的基础信息
    base = {
        "id": chunks[0].get("id", ""),
        "object": "chat.completion",
        "created": chunks[0].get("created", 0),
        "model": chunks[0].get("model", ""),
    }

    # 合并所有 delta 到 message
    content_parts = []
    reasoning_parts = []
    tool_calls_accum = {}

    finish_reason = None
    usage = None

    for chunk in chunks:
        choices = chunk.get("choices", [])
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta", {})

        # content
        if "content" in delta and delta["content"] is not None:
            content_parts.append(delta["content"])

        # reasoning / reasoning_content
        reasoning_val = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning_val is not None:
            reasoning_parts.append(reasoning_val)

        # tool_calls
        if "tool_calls" in delta and delta["tool_calls"] is not None:
            for tc_delta in delta["tool_calls"]:
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls_accum:
                    tool_calls_accum[idx] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                if "id" in tc_delta and tc_delta["id"]:
                    tool_calls_accum[idx]["id"] = tc_delta["id"]
                if "type" in tc_delta and tc_delta["type"]:
                    tool_calls_accum[idx]["type"] = tc_delta["type"]
                if "function" in tc_delta:
                    fn = tc_delta["function"]
                    if "name" in fn and fn["name"]:
                        tool_calls_accum[idx]["function"]["name"] += fn["name"]
                    if "arguments" in fn and fn["arguments"]:
                        tool_calls_accum[idx]["function"]["arguments"] += fn["arguments"]

        # finish_reason
        fr = choice.get("finish_reason")
        if fr is not None:
            finish_reason = fr

    # usage（最后一个 chunk 可能包含）
    usage = chunks[-1].get("usage")

    # 构建 message
    message = {"role": "assistant"}
    if content_parts:
        message["content"] = "".join(content_parts)
    else:
        message["content"] = ""
    if reasoning_parts:
        message["reasoning"] = "".join(reasoning_parts)
    if tool_calls_accum:
        message["tool_calls"] = [
            tool_calls_accum[i] for i in sorted(tool_calls_accum.keys())
        ]

    base["choices"] = [{
        "index": 0,
        "message": message,
        "finish_reason": finish_reason or "stop",
    }]
    if usage:
        base["usage"] = usage

    return base


def compare_stream(vllm_chunks: list, proxy_chunks: list, label: str) -> tuple:
    """对比流式 SSE 响应"""
    errors = []
    infos = []

    infos.append(f"【流式对比: {label}】")

    # chunk 数量对比
    vllm_count = len(vllm_chunks)
    proxy_count = len(proxy_chunks)
    infos.append(f"  chunk 数量: vllm={vllm_count}, proxy={proxy_count}")

    if vllm_count == 0:
        errors.append("vLLM 流式响应无有效 chunk")
        return errors, infos
    if proxy_count == 0:
        errors.append("proxy 流式响应无有效 chunk")
        return errors, infos

    # 重建完整响应并对比
    vllm_reconstructed = reconstruct_from_chunks(vllm_chunks)
    proxy_reconstructed = reconstruct_from_chunks(proxy_chunks)

    infos.append(f"  重建完整响应进行内容对比...")

    # 对比重建后的完整响应（使用非流式对比逻辑）
    sub_errors, sub_infos = compare_nonstream(vllm_reconstructed, proxy_reconstructed, f"{label}[stream重建]")
    errors.extend(sub_errors)
    infos.extend(sub_infos)

    # 逐 chunk 结构对比（前几个和后几个 chunk）
    # 只对比结构框架，不逐字对比内容（内容已通过重建对比覆盖）
    check_count = min(5, vllm_count, proxy_count)
    infos.append(f"  逐 chunk 结构对比 (前 {check_count} 个)...")

    for i in range(check_count):
        v_ch = vllm_chunks[i]
        p_ch = proxy_chunks[i]
        ch_path = f"chunk[{i}]"

        # object 字段
        compare_recursive(v_ch.get("object"), p_ch.get("object"), f"{ch_path}.object", errors, infos, skip_fields=STREAM_SKIP_FIELDS)

        # model 字段
        compare_recursive(v_ch.get("model"), p_ch.get("model"), f"{ch_path}.model", errors, infos, skip_fields=STREAM_SKIP_FIELDS)

        # choices 结构
        v_choices = v_ch.get("choices", [])
        p_choices = p_ch.get("choices", [])

        if len(v_choices) != len(p_choices):
            errors.append(f"{ch_path}.choices 镗度不一致 (vllm={len(v_choices)}, proxy={len(p_choices)})")
            continue

        for j, (vc, pc) in enumerate(zip(v_choices, p_choices)):
            c_path = f"{ch_path}.choices[{j}]"

            # index
            compare_recursive(vc.get("index"), pc.get("index"), f"{c_path}.index", errors, infos, skip_fields=STREAM_SKIP_FIELDS)

            # delta 字段集合（只检查字段名是否一致，跳过动态字段）
            v_delta_keys = set(vc.get("delta", {}).keys()) - STREAM_SKIP_FIELDS
            p_delta_keys = set(pc.get("delta", {}).keys()) - STREAM_SKIP_FIELDS

            v_missing = v_delta_keys - p_delta_keys
            p_extra = p_delta_keys - v_delta_keys

            if v_missing:
                errors.append(f"{c_path}.delta: proxy 缺失字段 {sorted(v_missing)}")
            if p_extra:
                infos.append(f"  {c_path}.delta: proxy 有额外字段 {sorted(p_extra)}")

            # finish_reason
            v_fr = vc.get("finish_reason")
            p_fr = pc.get("finish_reason")
            if v_fr is not None and p_fr is not None:
                compare_recursive(v_fr, p_fr, f"{c_path}.finish_reason", errors, infos, skip_fields=STREAM_SKIP_FIELDS)

    # 最后一个 chunk 的 usage 对比
    v_last = vllm_chunks[-1]
    p_last = proxy_chunks[-1]

    v_last_usage = v_last.get("usage")
    p_last_usage = p_last.get("usage")

    if v_last_usage and p_last_usage:
        compare_recursive(v_last_usage, p_last_usage, "final_chunk.usage", errors, infos)
    elif v_last_usage and not p_last_usage:
        errors.append("final_chunk.usage: vllm 有 usage, proxy 缺失")
    elif not v_last_usage and p_last_usage:
        infos.append(f"  final_chunk.usage: proxy 有额外 usage 字段 (vllm 无)")

    return errors, infos


# ========================================
# 主入口
# ========================================

def main():
    parser = argparse.ArgumentParser(description="trajproxy vs vLLM 响应对比工具")
    parser.add_argument("--mode", required=True, choices=["nonstream", "stream"],
                        help="对比模式: nonstream 或 stream")
    parser.add_argument("--vllm", required=True, help="vLLM 响应文件路径")
    parser.add_argument("--proxy", required=True, help="trajproxy 响应文件路径")
    parser.add_argument("--label", required=True, help="场景标签（用于日志）")

    args = parser.parse_args()

    # 读取输入文件
    with open(args.vllm, "r") as f:
        vllm_raw = f.read()
    with open(args.proxy, "r") as f:
        proxy_raw = f.read()

    if args.mode == "nonstream":
        try:
            vllm_data = json.loads(vllm_raw.strip())
        except json.JSONDecodeError as e:
            print(f"ERROR:vLLM 响应 JSON 解析失败: {e}")
            sys.exit(1)
        try:
            proxy_data = json.loads(proxy_raw.strip())
        except json.JSONDecodeError as e:
            print(f"ERROR:proxy 响应 JSON 解析失败: {e}")
            sys.exit(1)

        errors, infos = compare_nonstream(vllm_data, proxy_data, args.label)

    elif args.mode == "stream":
        vllm_chunks = parse_sse_chunks(vllm_raw)
        proxy_chunks = parse_sse_chunks(proxy_raw)

        if not vllm_chunks:
            print(f"ERROR:vLLM 流式响应无有效 SSE chunk")
            sys.exit(1)
        if not proxy_chunks:
            print(f"ERROR:proxy 流式响应无有效 SSE chunk")
            sys.exit(1)

        errors, infos = compare_stream(vllm_chunks, proxy_chunks, args.label)

    # 输出结果
    for line in infos:
        print(f"INFO:{line}")
    for err in errors:
        print(f"ERROR:{err}")

    # 返回码
    if errors:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()