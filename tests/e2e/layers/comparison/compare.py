#!/usr/bin/env python3
"""
trajproxy vs vLLM 响应对比工具

支持 OpenAI (chat/completions)、Claude (/v1/messages) 和 Responses (/v1/responses) 三种 API 格式。

用法:
    # OpenAI 格式
    python3 compare.py --mode nonstream --api openai --vllm vllm.json --proxy proxy.json --label "C101"
    python3 compare.py --mode stream     --api openai --vllm vllm_sse.txt --proxy proxy_sse.txt --label "C101"

    # Claude 格式
    python3 compare.py --mode nonstream --api claude --vllm vllm.json --proxy proxy.json --label "C201"
    python3 compare.py --mode stream     --api claude --vllm vllm_sse.txt --proxy proxy_sse.txt --label "C201"

    # Responses 格式
    python3 compare.py --mode nonstream --api responses --vllm vllm.json --proxy proxy.json --label "C301"
    python3 compare.py --mode stream     --api responses --vllm vllm_sse.txt --proxy proxy_sse.txt --label "C301"

核心设计:
    - 非流式对比: 解析 JSON，逐字段/逐 content block 递归对比
    - 流式对比: 解析 SSE events/chunks，合并后对比完整语义
    - 特殊 token 泄漏检测: 检查 content/reasoning/tool_calls 中是否有 <think>/<tool_call> 等标记残留
    - 占位符替换策略: 对于模型可能无法识别的特殊字符，先用占位符 (#think#) 替换，
      再通过 sed 二次编辑还原，确保对比时不会被特殊字符干扰
"""

import argparse
import difflib
import json
import re
import sys
from typing import Any, List, Tuple


# ========================================
# 配置
# ========================================

# OpenAI 格式需要跳过的运行时动态字段 + proxy 额外返回的字段
OPENAI_SKIP_FIELDS = {
    "id", "created",
    "reasoning",                  # vLLM 扩展字段, proxy 不一定返回, 允许缺失
    "reasoning_content",          # reasoning_parser 提取的思考内容
    "provider_specific_fields",   # reasoning 相关 provider 字段 (message/choice 级别)
    "logprobs",                   # proxy 补充的 logprobs (vLLM 未在顶层返回)
    "prompt_token_ids",           # proxy 额外返回的 prompt token IDs
    "kv_transfer_params",         # vLLM 内部参数, proxy 不透传
    "prompt_logprobs",            # 请求未启用时 vLLM 返回 null, proxy 省略
    "service_tier",               # vLLM 返回 null, proxy 省略
    "system_fingerprint",         # vLLM 返回 null, proxy 省略
    "completion_tokens_details",  # vLLM 含 reasoning_tokens 等, proxy 不保证透传
}
OPENAI_STREAM_SKIP_FIELDS = OPENAI_SKIP_FIELDS | {"seed"}

# Claude 格式需要跳过的运行时动态字段 + proxy 额外返回的字段
CLAUDE_SKIP_FIELDS = {
    "id",
    "provider_specific_fields",   # reasoning 相关 provider 字段
    "logprobs",                   # proxy 补充的 logprobs
    "prompt_token_ids",           # proxy 额外返回的 prompt token IDs
}

# Responses 格式需要跳过的运行时动态字段
RESPONSES_SKIP_FIELDS = {
    "id",                          # 每条 response/output item 的 id 都是运行时生成
    "created_at",                  # 时间戳
    "completed_at",                # 时间戳
    "incomplete_details",          # 完成细节
    "error",                       # 错误信息（单独处理）
    "parallel_tool_calls",         # 并行工具调用标志
    "truncation",                  # 截断策略
    "background",                  # 后台执行标志
    "metadata",                    # 用户自定义元数据
    "temperature",                 # 采样参数（回显）
    "top_p",                       # 采样参数（回显）
    "max_output_tokens",           # 采样参数（回显）
    "reasoning",                   # 推理配置（回显）
    "tool_choice",                 # 工具选择策略（回显）
}

# ========================================
# 禁止出现在最终 API 响应中的特殊 token
# ========================================
FORBIDDEN_TOKENS = [
    '<think>', '</think>',
    '<|tool_call_begin|>', '<|tool_call_end|>',
    '<tool_call>', '</tool_call>',
    '<function>', '</function>',
    '<parameter>', '</parameter>',
    '<scratch_pad>', '</scratch_pad>',
]

FORBIDDEN_PATTERNS = [
    re.compile(r"<function=\w+>"),
    re.compile(r"<parameter=\w+>"),
]

# 占位符映射：用于在对比前替换特殊字符，避免模型格式化干扰
# 使用者：在请求体中用占位符代替原始标记，trajproxy 内部会反向替换
PLACEHOLDER_MAP = {
    '#think#': '<think>',
    '#/think#': '</think>',
    '#tool_call#': '<tool_call>',
    '#/tool_call#': '</tool_call>',
    '#tool_call_begin#': '<|tool_call_begin|>',
    '#tool_call_end#': '<|tool_call_end|>',
}


def check_forbidden_tokens(text: str, field_path: str, errors: list, infos: list) -> None:
    """检查文本中是否包含禁止的特殊 token"""
    if not text or not isinstance(text, str):
        return
    found = []
    for tok in FORBIDDEN_TOKENS:
        if tok in text:
            found.append(tok)
    for pat in FORBIDDEN_PATTERNS:
        for m in pat.findall(text):
            if m not in found:
                found.append(m)
    if found:
        errors.append(f"{field_path}: 包含禁止的特殊token {found}")
    else:
        infos.append(f"  {field_path}: 无禁止特殊token泄漏 ✓")


def detect_stream_error_chunks(chunks: list, label: str = "stream") -> list:
    """检测流式 chunk 中是否包含 error 字段

    某些模型在流式响应中会以 data: {"error": {...}} 格式返回错误,
    这些 chunk 没有 choices 字段, 在对比时会被静默跳过.
    此函数显式检测并报告这些错误.

    Returns: list of error strings found
    """
    found = []
    for i, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        err = chunk.get("error")
        if err:
            if isinstance(err, dict):
                msg = err.get("message", str(err))
                code = err.get("code", "unknown")
                found.append(f"{label} chunk[{i}].error: code={code}, message={msg}")
            else:
                found.append(f"{label} chunk[{i}].error: {err}")
    return found


def detect_nonstream_error(data: dict, source: str, errors: list) -> bool:
    """检测非流式响应中是否包含 error 字段

    Returns: True if error found (已追加到 errors), False otherwise
    """
    if not isinstance(data, dict):
        return False
    err = data.get("error")
    if not err:
        return False
    if isinstance(err, dict):
        msg = err.get("message", str(err))
        code = err.get("code", "unknown")
        errors.append(f"{source} 响应包含 error: code={code}, message={msg}")
    else:
        errors.append(f"{source} 响应包含 error: {err}")
    return True


# Proxy 专属增强字段：只需验证 proxy 侧存在且有效
PROXY_ONLY_FIELDS = {
    "choices[0].message.reasoning_content": "nonempty_string",
    "choices[0].message.provider_specific_fields": "nonempty_dict",
    "choices[0].provider_specific_fields": "nonempty_dict",
}


def _extract_by_path(data: dict, path: str):
    """从嵌套 dict 中按路径提取值，如 'choices[0].message.reasoning_content'"""
    parts = path.split('.')
    current = data
    for part in parts:
        m = re.match(r'(.+)\[(\d+)\]$', part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
    return current


def _validate_proxy_fields(proxy_data: dict, fields: dict, errors: list, infos: list,
                           vllm_data: dict = None) -> None:
    """验证 proxy 专属增强字段的存在性和有效性

    Args:
        vllm_data: 可选，vLLM 响应数据，用于判断是否需要校验 reasoning_content
    """
    # 判断 vLLM 是否产生了 reasoning，决定是否需要校验 proxy 的 reasoning_content
    vllm_has_reasoning = False
    if vllm_data is not None:
        v_choices = vllm_data.get("choices", [])
        if v_choices:
            v_msg = v_choices[0].get("message", {})
            if v_msg.get("reasoning") or v_msg.get("reasoning_content"):
                vllm_has_reasoning = True

    for path, vtype in fields.items():
        # reasoning_content 仅在 vLLM 有 reasoning 时才要求 proxy 提供
        if "reasoning_content" in path and not vllm_has_reasoning:
            infos.append(f"  {path}: vLLM 无 reasoning, 跳过校验 ✓")
            continue
        val = _extract_by_path(proxy_data, path)
        if val is None:
            errors.append(f"{path}: proxy 缺失")
            continue
        if vtype == "nonempty_dict":
            if isinstance(val, dict) and val:
                infos.append(f"  {path}: proxy 存在 ✓ (keys={len(val)})")
            else:
                errors.append(f"{path}: proxy 值为空 dict 或类型不匹配")
        elif vtype == "nonempty_string":
            if isinstance(val, str) and val.strip():
                infos.append(f"  {path}: proxy 存在 ✓ (len={len(val)})")
            else:
                errors.append(f"{path}: proxy 值为空字符串或类型不匹配")
        elif vtype == "nonempty_list":
            if isinstance(val, list) and val:
                infos.append(f"  {path}: proxy 存在 ✓ (len={len(val)})")
            else:
                errors.append(f"{path}: proxy 值为空 list 或类型不匹配")
        elif vtype == "has_content_array":
            if isinstance(val, dict) and isinstance(val.get("content"), list) and val["content"]:
                infos.append(f"  {path}: proxy 存在 ✓ (content items={len(val['content'])})")
            else:
                errors.append(f"{path}: proxy 无有效 content 数组")


def _validate_provider_reasoning(proxy_data: dict, errors: list, infos: list) -> None:
    """校验 provider_specific_fields.reasoning（可选字段，缺失不报错）"""
    p_choices = proxy_data.get("choices", [])
    if not p_choices:
        return
    choice = p_choices[0]

    # choice 级 provider_specific_fields.reasoning
    psf_c = choice.get("provider_specific_fields")
    if isinstance(psf_c, dict) and isinstance(psf_c.get("reasoning"), str) and psf_c["reasoning"].strip():
        check_forbidden_tokens(psf_c["reasoning"],
                               "choices[0].provider_specific_fields.reasoning", errors, infos)
        infos.append("  choices[0].provider_specific_fields.reasoning: 有效 ✓")
    else:
        infos.append("  choices[0].provider_specific_fields.reasoning: 缺失或为空（跳过）")

    # message 级 provider_specific_fields.reasoning
    p_msg = choice.get("message", {})
    psf_m = p_msg.get("provider_specific_fields")
    if isinstance(psf_m, dict) and isinstance(psf_m.get("reasoning"), str) and psf_m["reasoning"].strip():
        check_forbidden_tokens(psf_m["reasoning"],
                               "choices[0].message.provider_specific_fields.reasoning", errors, infos)
        infos.append("  choices[0].message.provider_specific_fields.reasoning: 有效 ✓")
    else:
        infos.append("  choices[0].message.provider_specific_fields.reasoning: 缺失或为空（跳过）")


def strip_special_tokens(text: str) -> str:
    """剥离文本中的所有禁止特殊 token"""
    if not text or not isinstance(text, str):
        return text
    for tok in FORBIDDEN_TOKENS:
        text = text.replace(tok, "")
    for pat in FORBIDDEN_PATTERNS:
        text = pat.sub("", text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def apply_placeholders(data: dict, direction: str = "to_real") -> dict:
    """占位符双向转换

    Args:
        direction: "to_real" 将占位符转换为真实标记; "to_placeholder" 反之
    """
    if direction == "to_real":
        mapping = PLACEHOLDER_MAP
    else:
        mapping = {v: k for k, v in PLACEHOLDER_MAP.items()}

    def _replace(s):
        if not isinstance(s, str):
            return s
        for old, new in mapping.items():
            s = s.replace(old, new)
        return s

    raw = json.dumps(data, ensure_ascii=False)
    for old, new in mapping.items():
        raw = raw.replace(old, new)
    return json.loads(raw)


# ========================================
# 通用对比原语
# ========================================

def compare_recursive(v, p, path: str, errors: list, infos: list,
                      skip_fields: set = None, depth: int = 0,
                      extra_skip_paths: set = None) -> None:
    """递归对比两个值的每个字段

    Args:
        extra_skip_paths: 路径子串集合，若当前 path 包含其中任一子串则跳过对比
    """
    skip_fields = skip_fields or set()
    extra_skip_paths = extra_skip_paths or set()

    # 路径级跳过：若当前路径包含 extra_skip_paths 中的任一子串，不做对比
    if extra_skip_paths and any(sp in path for sp in extra_skip_paths):
        infos.append(f"  {path}: 已配置跳过对比 ✓")
        return

    # content 字段宽松比较：None 和 "" 在 tool_calls 场景下语义等价
    # OpenAI spec 中 tool_calls 时 content 可 null 或 ""，均表示"无文本内容"
    if path.endswith(".content") and v in (None, "") and p in (None, ""):
        infos.append(f"  {path}: 值等价 (空内容, vllm={repr(v)}, proxy={repr(p)}) ✓")
        return

    if v is None and p is None:
        return
    if v is None:
        infos.append(f"  {path}: vllm=None, proxy={repr(p)[:80]}")
        return
    if p is None:
        errors.append(f"{path}: vllm={repr(v)[:80]}, proxy=None")
        return

    if isinstance(v, dict) and isinstance(p, dict):
        v_keys = set(v.keys()) - skip_fields
        p_keys = set(p.keys()) - skip_fields
        for k in sorted(v_keys - p_keys):
            if v[k] is None:
                infos.append(f"  {path}.{k}: vllm=null, proxy 省略 ✓")
                continue
            errors.append(f"{path}.{k}: vllm 有此字段, proxy 缺失")
        for k in sorted(p_keys - v_keys):
            infos.append(f"  {path}.{k}: proxy 有此字段, vllm 缺失 (可能是新增)")
        for k in sorted(v_keys & p_keys):
            compare_recursive(v[k], p[k], f"{path}.{k}", errors, infos, skip_fields, depth + 1,
                              extra_skip_paths)

    elif isinstance(v, list) and isinstance(p, list):
        if len(v) != len(p):
            errors.append(f"{path}: 数组长度不一致 (vllm={len(v)}, proxy={len(p)})")
            # 仍然尝试对比前 min(len) 个元素
        for i in range(min(len(v), len(p))):
            compare_recursive(v[i], p[i], f"{path}[{i}]", errors, infos, skip_fields, depth + 1,
                              extra_skip_paths)

    elif isinstance(v, float) and isinstance(p, float):
        if abs(v - p) > 1e-6:
            errors.append(f"{path}: 数值不一致 (vllm={v}, proxy={p})")

    elif v != p:
        # 对于字符串，如果不是相等但可能是等价（去空白），报 info 而非 error
        if isinstance(v, str) and isinstance(p, str):
            if v.strip() == p.strip():
                infos.append(f"  {path}: 值仅空白差异 ✓ (len: vllm={len(v)}, proxy={len(p)})")
                return
        errors.append(f"{path}: 值不一致")
        errors.append(f"  vllm:  {repr(v)[:120]}")
        errors.append(f"  proxy: {repr(p)[:120]}")


USAGE_SKIP_COMPARE = {"prompt_tokens", "completion_tokens", "total_tokens"}


def _validate_usage_positive(v_usage, p_usage, path: str, errors: list, infos: list) -> None:
    """Claude 格式 usage 仅验证 > 0，不比对值一致（两路经不同转换层，prompt 可能不同）

    与 compare_usage 不同，此函数不递归比对剩余字段，只确保 token 计数合理。
    """
    if not isinstance(v_usage, dict) or not isinstance(p_usage, dict):
        errors.append(f"{path}: 格式异常 (vllm={type(v_usage).__name__}, proxy={type(p_usage).__name__})")
        return

    for key in ["input_tokens", "output_tokens"]:
        v_val = v_usage.get(key, 0)
        p_val = p_usage.get(key, 0)
        if isinstance(v_val, (int, float)) and v_val > 0 and isinstance(p_val, (int, float)) and p_val > 0:
            infos.append(f"  {path}.{key}: >0 ✓ (vllm={v_val}, proxy={p_val})")
        else:
            errors.append(f"{path}.{key}: 应大于0 (vllm={v_val}, proxy={p_val})")


def compare_usage(v_usage, p_usage, path: str, errors: list, infos: list,
                   skip_fields: set = None) -> None:
    """对比 token usage（token 计数只验证 >0，其余字段递归对比）"""
    skip = skip_fields or set()

    if isinstance(v_usage, dict) and isinstance(p_usage, dict):
        for key in sorted(USAGE_SKIP_COMPARE):
            if key in skip:
                continue
            v_val = v_usage.get(key)
            p_val = p_usage.get(key)
            if v_val is not None and p_val is not None:
                if isinstance(v_val, (int, float)) and v_val > 0 and isinstance(p_val, (int, float)) and p_val > 0:
                    infos.append(f"  {path}.{key}: >0 ✓ (vllm={v_val}, proxy={p_val})")
                else:
                    errors.append(f"{path}.{key}: 应大于0 (vllm={v_val}, proxy={p_val})")

        remaining_v = dict(v_usage)
        remaining_p = dict(p_usage)
        for key in USAGE_SKIP_COMPARE:
            remaining_v.pop(key, None)
            remaining_p.pop(key, None)
        compare_recursive(remaining_v, remaining_p, path, errors, infos, skip)
    else:
        compare_recursive(v_usage, p_usage, path, errors, infos, skip)


# ========================================
# OpenAI 格式对比
# ========================================

def compare_openai_nonstream(vllm_data: dict, proxy_data: dict, label: str,
                              with_reasoning: bool = False,
                              extra_skip_paths: set = None) -> Tuple[list, list]:
    """对比 OpenAI chat/completions 非流式响应

    Args:
        extra_skip_paths: 路径子串集合，匹配的路径跳过值对比（如 {"finish_reason", "arguments"}）
    """
    errors, infos = [], []
    infos.append(f"[{label}] OpenAI 非流式对比")

    detect_nonstream_error(vllm_data, "vllm", errors)
    detect_nonstream_error(proxy_data, "proxy", errors)
    if errors:
        infos.append(f"  非流式响应包含 error, 跳过后续对比")
        return errors, infos

    # 顶层结构（排除 usage，usage 由下方 compare_usage 单独处理，仅验证 >0）
    v_top = {k: v for k, v in vllm_data.items() if k != "usage"}
    p_top = {k: v for k, v in proxy_data.items() if k != "usage"}
    compare_recursive(v_top, p_top, "", errors, infos, OPENAI_SKIP_FIELDS,
                      extra_skip_paths=extra_skip_paths)

    # Proxy 专属增强字段验证（仅在有 reasoning_parser 时校验）
    if with_reasoning:
        _validate_proxy_fields(proxy_data, PROXY_ONLY_FIELDS, errors, infos)
        _validate_provider_reasoning(proxy_data, errors, infos)

    # choices 对比
    v_choices = vllm_data.get("choices", [])
    p_choices = proxy_data.get("choices", [])
    if v_choices and p_choices:
        v_msg = v_choices[0].get("message", {})
        p_msg = p_choices[0].get("message", {})

        # content 特殊 token 检查
        check_forbidden_tokens(p_msg.get("content", ""), "choices[0].message.content", errors, infos)
        check_forbidden_tokens(p_msg.get("reasoning_content", ""), "choices[0].message.reasoning_content", errors, infos)

        # tool_calls 检查
        v_tools = v_msg.get("tool_calls", [])
        p_tools = p_msg.get("tool_calls", [])
        if p_tools:
            for i, tc in enumerate(p_tools):
                fc = tc.get("function", {})
                check_forbidden_tokens(fc.get("name", ""), f"choices[0].message.tool_calls[{i}].function.name", errors, infos)
                args_str = fc.get("arguments", "")
                check_forbidden_tokens(args_str, f"choices[0].message.tool_calls[{i}].function.arguments", errors, infos)
                # 验证 arguments 是合法 JSON
                if args_str:
                    try:
                        json.loads(args_str)
                        infos.append(f"  choices[0].message.tool_calls[{i}].arguments: 合法 JSON ✓")
                    except json.JSONDecodeError:
                        errors.append(f"choices[0].message.tool_calls[{i}].arguments: 不是合法 JSON")

    # usage 对比
    v_usage = vllm_data.get("usage")
    p_usage = proxy_data.get("usage")
    if v_usage and p_usage:
        compare_usage(v_usage, p_usage, "usage", errors, infos, OPENAI_SKIP_FIELDS)

    return errors, infos


def parse_openai_sse(raw: str) -> list:
    """解析 OpenAI SSE 格式: data: {...}\\n\\ndata: [DONE]"""
    chunks = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if line.startswith("data: ") and not line.endswith("[DONE]"):
            try:
                chunks.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return chunks


def compare_openai_stream(vllm_raw: str, proxy_raw: str, label: str,
                          extra_skip_paths: set = None) -> Tuple[list, list]:
    """对比 OpenAI chat/completions 流式响应

    Args:
        extra_skip_paths: 路径子串集合，匹配的路径跳过值对比（如 {"content"}）
    """
    extra_skip_paths = extra_skip_paths or set()
    errors, infos = [], []
    infos.append(f"[{label}] OpenAI 流式对比")

    v_chunks = parse_openai_sse(vllm_raw)
    p_chunks = parse_openai_sse(proxy_raw)

    v_errs = detect_stream_error_chunks(v_chunks, "vllm")
    p_errs = detect_stream_error_chunks(p_chunks, "proxy")
    for e in v_errs:
        errors.append(e)
    for e in p_errs:
        errors.append(e)
    if v_errs or p_errs:
        infos.append(f"  流式响应包含 error chunk, 跳过后续对比")
        return errors, infos

    if not v_chunks:
        errors.append("vLLM 流式响应无有效 SSE chunk")
        return errors, infos
    if not p_chunks:
        errors.append("proxy 流式响应无有效 SSE chunk")
        return errors, infos

    # 逐 chunk 对比（跳过 delta 中无法对齐的字段）
    for i, (vc, pc) in enumerate(zip(v_chunks, p_chunks)):
        prefix = f"chunk[{i}]"
        v_choices = vc.get("choices", [])
        p_choices = pc.get("choices", [])

        if len(v_choices) != len(p_choices):
            errors.append(f"{prefix}.choices 长度不一致 (vllm={len(v_choices)}, proxy={len(p_choices)})")
            continue

        for j, (vch, pch) in enumerate(zip(v_choices, p_choices)):
            cp = f"{prefix}.choices[{j}]"
            v_fr = vch.get("finish_reason")
            p_fr = pch.get("finish_reason")
            if v_fr is not None and p_fr is not None and v_fr != p_fr:
                errors.append(f"{cp}.finish_reason: vllm={v_fr}, proxy={p_fr}")

    # 合并后对比 content 和 reasoning_content
    v_content = "".join(
        c.get("choices", [{}])[0].get("delta", {}).get("content", "")
        for c in v_chunks
    )
    p_content = "".join(
        c.get("choices", [{}])[0].get("delta", {}).get("content", "")
        for c in p_chunks
    )
    

    # 去除空白后对比
    if "content" in extra_skip_paths:
        infos.append(f"  合并后 content: 已配置跳过对比 ✓ (vllm_len={len(v_content)}, proxy_len={len(p_content)})")
    elif v_content.strip() != p_content.strip():
        diff_stats = compute_diff_stats(v_content, p_content)
        errors.append(f"合并后 content 不一致 (vllm={diff_stats['vllm_len']}, proxy={diff_stats['proxy_len']}, "
                      f"word_similarity={diff_stats['word_similarity']:.2f})")
    else:
        infos.append(f"  合并后 content 一致 ✓ (len={len(p_content)})")

    # 特殊 token 检查
    check_forbidden_tokens(p_content, "合并后 content", errors, infos)

    return errors, infos


# ========================================
# Claude 格式对比
# ========================================

def _get_claude_blocks(content_list: list) -> dict:
    """将 Claude content[] 按 type 分类"""
    blocks = {"text": [], "thinking": [], "tool_use": [], "other": []}
    for block in (content_list or []):
        t = block.get("type", "other")
        blocks.get(t, blocks["other"]).append(block)
    return blocks


def compare_claude_nonstream(vllm_data: dict, proxy_data: dict, label: str) -> Tuple[list, list]:
    """对比 Claude /v1/messages 非流式响应"""
    errors, infos = [], []
    infos.append(f"[{label}] Claude 非流式对比")

    detect_nonstream_error(vllm_data, "vllm", errors)
    detect_nonstream_error(proxy_data, "proxy", errors)
    if errors:
        infos.append(f"  非流式响应包含 error, 跳过后续对比")
        return errors, infos

    # 跳过运行时字段
    skip = CLAUDE_SKIP_FIELDS

    # 顶层字段对比
    for key in ["type", "role", "model", "stop_reason", "stop_sequence"]:
        compare_recursive(vllm_data.get(key), proxy_data.get(key), key, errors, infos, skip)

    # content[] 对比
    v_blocks = _get_claude_blocks(vllm_data.get("content", []))
    p_blocks = _get_claude_blocks(proxy_data.get("content", []))

    for btype in ["text", "thinking", "tool_use"]:
        v_list = v_blocks[btype]
        p_list = p_blocks[btype]

        if not v_list and not p_list:
            continue

        # content block 类型缺失：两路经不同转换层（vLLM 内部 vs LiteLLM），
        # block 结构可能不同，降级为 INFO 而非 ERROR
        if not v_list:
            infos.append(f"  content.{btype}: vllm 有此类型, proxy 缺失 (转换层差异，可接受)")
            continue
        if not p_list:
            infos.append(f"  content.{btype}: proxy 有此类型, vllm 缺失 (转换层差异，可接受)")
            continue

        # 按数量对比：转换层差异可导致数量不同，降级为 INFO
        if len(v_list) != len(p_list):
            infos.append(f"  content.{btype}: 数量不同 (vllm={len(v_list)}, proxy={len(p_list)}, 转换层差异)")

        for i in range(min(len(v_list), len(p_list))):
            vb, pb = v_list[i], p_list[i]
            prefix = f"content[{btype}][{i}]"

            if btype == "text":
                # Claude 格式经不同转换层，text 内容可能因 prompt 差异而不同
                # 仅验证: 存在性 + 无禁止 token 泄漏
                v_text = vb.get("text", "")
                p_text = pb.get("text", "")
                infos.append(f"  {prefix}.text: 存在 ✓ (vllm_len={len(v_text)}, proxy_len={len(p_text)})")
                check_forbidden_tokens(p_text, f"{prefix}.text", errors, infos)

            elif btype == "thinking":
                # Claude 格式经不同转换层，thinking 内容因 prompt 差异必然不同
                # 仅验证: 非空 + 无禁止 token 泄漏
                v_th = vb.get("thinking", "")
                p_th = pb.get("thinking", "")
                if p_th:
                    infos.append(f"  {prefix}.thinking: 存在 ✓ (vllm_len={len(v_th)}, proxy_len={len(p_th)})")
                else:
                    errors.append(f"{prefix}.thinking: proxy 缺失 thinking 内容")
                check_forbidden_tokens(p_th, f"{prefix}.thinking", errors, infos)

            elif btype == "tool_use":
                # tool_use 是功能性核心，保持严格比对
                compare_recursive(vb.get("name"), pb.get("name"), f"{prefix}.name", errors, infos, skip)
                # tool input 对比
                compare_recursive(vb.get("input"), pb.get("input"), f"{prefix}.input", errors, infos, skip)
                # 检查 input 的 JSON 序列化中是否包含特殊 token
                if pb.get("input"):
                    input_str = json.dumps(pb["input"], ensure_ascii=False)
                    check_forbidden_tokens(input_str, f"{prefix}.input (JSON)", errors, infos)

    # usage 对比：Claude 格式经不同转换层，token 计数可能因 prompt 差异而不同
    # 仅验证 > 0，不比对值一致
    v_usage = vllm_data.get("usage")
    p_usage = proxy_data.get("usage")
    if v_usage and p_usage:
        _validate_usage_positive(v_usage, p_usage, "usage", errors, infos)

    return errors, infos


def parse_claude_sse(raw: str) -> dict:
    """解析 Claude SSE 格式: event: xxx\\ndata: {...}\\n\\n

    Returns:
        list of (event_name, event_data) tuples (保持时间顺序)
    """
    events = []
    current_event = None
    current_data = ""

    for line in raw.strip().split("\n"):
        line_stripped = line.strip()
        if line_stripped.startswith("event: "):
            if current_event and current_data:
                try:
                    events.append((current_event, json.loads(current_data)))
                except json.JSONDecodeError:
                    pass
            current_event = line_stripped[7:].strip()
            current_data = ""
        elif line_stripped.startswith("data: "):
            current_data = line_stripped[6:].strip()
        elif line_stripped == "":
            if current_event and current_data:
                try:
                    events.append((current_event, json.loads(current_data)))
                except json.JSONDecodeError:
                    pass
                current_event = None
                current_data = ""

    if current_event and current_data:
        try:
            events.append((current_event, json.loads(current_data)))
        except json.JSONDecodeError:
            pass

    return events


def compare_claude_stream(vllm_raw: str, proxy_raw: str, label: str) -> Tuple[list, list]:
    """对比 Claude /v1/messages 流式响应"""
    errors, infos = [], []
    infos.append(f"[{label}] Claude 流式对比")

    v_events_list = parse_claude_sse(vllm_raw)
    p_events_list = parse_claude_sse(proxy_raw)

    # 检测流式 error 事件 (event: error) 和 chunk 中的 error 字段
    for source, events in [("vllm", v_events_list), ("proxy", p_events_list)]:
        for evt_name, evt_data in events:
            if evt_name == "error":
                msg = evt_data.get("message", str(evt_data)) if isinstance(evt_data, dict) else str(evt_data)
                errors.append(f"{source} 流式 error 事件: {msg}")

    # 也检测非标准 error 字段 (某些实现将 error 嵌入 data)
    for source, events in [("vllm", v_events_list), ("proxy", p_events_list)]:
        for evt_name, evt_data in events:
            if isinstance(evt_data, dict) and "error" in evt_data and evt_name != "error":
                err = evt_data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                errors.append(f"{source} 流式 {evt_name} 事件包含 error 字段: {msg}")

    if errors:
        infos.append(f"  流式响应包含 error, 跳过后续对比")
        return errors, infos

    # 构建事件名集合用于必要事件检查
    v_event_names = {e[0] for e in v_events_list}
    p_event_names = {e[0] for e in p_events_list}

    # 验证必要的事件类型存在
    required_events = ["message_start", "message_stop"]
    for evt in required_events:
        if evt not in v_event_names:
            errors.append(f"vLLM 流式缺少 {evt} 事件")
        if evt not in p_event_names:
            errors.append(f"proxy 流式缺少 {evt} 事件")

    # 从 content_block_start/content_block_delta 中合并 content blocks
    v_merged = _merge_claude_stream(v_events_list)
    p_merged = _merge_claude_stream(p_events_list)

    # 对比合并后的 content blocks
    v_blocks = _get_claude_blocks(v_merged)
    p_blocks = _get_claude_blocks(p_merged)

    for btype in ["text", "thinking", "tool_use"]:
        v_list = v_blocks[btype]
        p_list = p_blocks[btype]

        if not v_list and not p_list:
            continue

        # content block 类型缺失：转换层差异，降级为 INFO
        if not v_list:
            infos.append(f"  {btype}: vllm 有此类型, proxy 缺失 (转换层差异，可接受)")
            continue
        if not p_list:
            infos.append(f"  {btype}: proxy 有此类型, vllm 缺失 (转换层差异，可接受)")
            continue

        # 合并所有同类型块的文本
        if btype == "text":
            # Claude 格式经不同转换层，text 内容可能因 prompt 差异而不同
            # 仅验证: 存在性 + 无禁止 token 泄漏
            p_text = "".join(b.get("text", "") for b in p_list)
            v_text = "".join(b.get("text", "") for b in v_list)
            infos.append(f"  合并后 text: 存在 ✓ (vllm_len={len(v_text)}, proxy_len={len(p_text)})")
            check_forbidden_tokens(p_text, "合并后 text (stream)", errors, infos)

        elif btype == "thinking":
            # Claude 格式经不同转换层，thinking 内容因 prompt 差异必然不同
            # 仅验证: 非空 + 无禁止 token 泄漏
            p_th = "".join(b.get("thinking", "") for b in p_list)
            if p_th.strip():
                infos.append(f"  合并后 thinking: 存在 ✓ (len={len(p_th)})")
            else:
                errors.append(f"合并后 thinking: proxy 缺失 thinking 内容")
            check_forbidden_tokens(p_th, "合并后 thinking (stream)", errors, infos)

        elif btype == "tool_use":
            # tool_use 是功能性核心，保持严格比对
            if len(v_list) != len(p_list):
                errors.append(f"tool_use 数量不一致 (vllm={len(v_list)}, proxy={len(p_list)})")
            for i in range(min(len(v_list), len(p_list))):
                prefix = f"tool_use[{i}]"
                compare_recursive(v_list[i].get("name"), p_list[i].get("name"),
                                  f"{prefix}.name", errors, infos)
                compare_recursive(v_list[i].get("input"), p_list[i].get("input"),
                                  f"{prefix}.input", errors, infos)
                if p_list[i].get("input"):
                    input_str = json.dumps(p_list[i]["input"], ensure_ascii=False)
                    check_forbidden_tokens(input_str, f"{prefix}.input (JSON)", errors, infos)

    # usage 对比（在 message_stop 或 message_delta 中）
    v_usage = {}
    p_usage = {}
    for evt_name, evt_data in v_events_list:
        if evt_name == "message_delta":
            v_usage = evt_data.get("usage", {})
            break
    for evt_name, evt_data in p_events_list:
        if evt_name == "message_delta":
            p_usage = evt_data.get("usage", {})
            break
    if not v_usage:
        for evt_name, evt_data in v_events_list:
            if evt_name == "message_stop" and evt_data.get("usage"):
                v_usage = evt_data["usage"]
                break
    if not p_usage:
        for evt_name, evt_data in p_events_list:
            if evt_name == "message_stop" and evt_data.get("usage"):
                p_usage = evt_data["usage"]
                break
    if v_usage and p_usage:
        _validate_usage_positive(v_usage, p_usage, "usage (stream)", errors, infos)

    return errors, infos


def _merge_claude_stream(events: dict) -> list:
    """从 Claude SSE events list 中合并出完整的 content[] 数组

    Args:
        events: list of (event_name, event_data) tuples

    Returns:
        list of content block dicts
    """
    blocks = []
    current_block = None

    for evt_name, evt_data in events:
        if evt_name == "content_block_start":
            block_info = evt_data.get("content_block", {})
            if block_info:
                current_block = dict(block_info)
                blocks.append(current_block)
        elif evt_name == "content_block_delta":
            delta = evt_data.get("delta", {})
            if current_block and delta:
                dtype = delta.get("type", "")
                if dtype == "text_delta":
                    current_block["text"] = (current_block.get("text", "") +
                                             delta.get("text", ""))
                elif dtype == "thinking_delta":
                    current_block["thinking"] = (current_block.get("thinking", "") +
                                                  delta.get("thinking", ""))
                elif dtype == "input_json_delta":
                    # tool_use 的 input 是增量 JSON 字符串，需拼接后解析
                    current_block["_input_json"] = (current_block.get("_input_json", "") +
                                                     delta.get("partial_json", ""))

    # 解析 tool_use 的 input_json
    for block in blocks:
        if block.get("type") == "tool_use" and block.get("_input_json"):
            try:
                block["input"] = json.loads(block["_input_json"])
            except json.JSONDecodeError:
                pass
            block.pop("_input_json", None)

    return blocks


# ========================================
# Responses 格式对比
# ========================================

# Responses output item 需要跳过的字段（每条 item 的 id 都是运行时生成）
_RESPONSES_OUTPUT_SKIP = {"id"}


def _get_responses_output_by_type(output_list: list) -> dict:
    """将 Responses output[] 按 type 分类

    Returns:
        dict: {"reasoning": [...], "message": [...], "function_call": [...], "other": [...]}
    """
    result = {"reasoning": [], "message": [], "function_call": [], "other": []}
    for item in (output_list or []):
        t = item.get("type", "other")
        result.get(t, result["other"]).append(item)
    return result


def _compare_responses_output(v_output: list, p_output: list, label: str,
                               errors: list, infos: list,
                               extra_skip_paths: set = None) -> None:
    """对比 Responses output[] 数组（非流式和流式共享的核心逻辑）

    策略: 与 Claude 对比类似——经转码层后文本/推理内容可能因 prompt 差异不完全一致，
    但 function_call 是功能性核心，需严格比对。
    """
    extra_skip_paths = extra_skip_paths or set()
    v_by_type = _get_responses_output_by_type(v_output)
    p_by_type = _get_responses_output_by_type(p_output)

    for btype in ["reasoning", "function_call", "message"]:
        v_list = v_by_type[btype]
        p_list = p_by_type[btype]

        if not v_list and not p_list:
            continue

        # type 缺失：转换层差异，降级为 INFO
        if not v_list:
            infos.append(f"  output.{btype}: vllm 有此类型, proxy 缺失 (转换层差异，可接受)")
            continue
        if not p_list:
            infos.append(f"  output.{btype}: proxy 有此类型, vllm 缺失 (转换层差异，可接受)")
            continue

        # 数量对比（转换层差异可导致数量不同，降级为 INFO）
        if len(v_list) != len(p_list):
            infos.append(f"  output.{btype}: 数量不同 (vllm={len(v_list)}, proxy={len(p_list)}, 转换层差异)")

        for i in range(min(len(v_list), len(p_list))):
            vi, pi = v_list[i], p_list[i]
            prefix = f"output[{btype}][{i}]"

            if btype == "reasoning":
                # reasoning: 仅验证 summary 存在 + 无禁止 token
                v_summaries = vi.get("summary", [])
                p_summaries = pi.get("summary", [])
                if p_summaries:
                    p_text = "".join(s.get("text", "") for s in p_summaries)
                    if p_text.strip():
                        infos.append(f"  {prefix}.summary: 存在 ✓ (vllm_count={len(v_summaries)}, proxy_count={len(p_summaries)})")
                    else:
                        errors.append(f"{prefix}.summary: proxy reasoning 文本为空")
                    check_forbidden_tokens(p_text, f"{prefix}.summary", errors, infos)
                else:
                    errors.append(f"{prefix}: proxy 缺失 reasoning summary")

            elif btype == "function_call":
                # function_call: 功能性核心，严格比对 name + arguments
                compare_recursive(vi.get("name"), pi.get("name"),
                                  f"{prefix}.name", errors, infos, _RESPONSES_OUTPUT_SKIP,
                                  extra_skip_paths=extra_skip_paths)
                # arguments 对比（都是 JSON 字符串）
                v_args_str = vi.get("arguments", "")
                p_args_str = pi.get("arguments", "")
                if "arguments" not in extra_skip_paths:
                    if v_args_str and p_args_str:
                        # 尝试 JSON 解析后对比（忽略格式差异）
                        try:
                            v_args = json.loads(v_args_str)
                            p_args = json.loads(p_args_str)
                            compare_recursive(v_args, p_args, f"{prefix}.arguments",
                                              errors, infos, set(),
                                              extra_skip_paths=extra_skip_paths)
                            infos.append(f"  {prefix}.arguments: 合法 JSON ✓")
                        except json.JSONDecodeError:
                            # JSON 解析失败，退化为字符串对比
                            if v_args_str.strip() != p_args_str.strip():
                                errors.append(f"{prefix}.arguments: 值不一致 (JSON 解析失败)")
                    elif v_args_str or p_args_str:
                        errors.append(f"{prefix}.arguments: 一方为空 (vllm_len={len(v_args_str)}, proxy_len={len(p_args_str)})")
                check_forbidden_tokens(p_args_str, f"{prefix}.arguments", errors, infos)
                # call_id 仅验证前缀格式
                p_call_id = pi.get("call_id", "")
                if p_call_id:
                    infos.append(f"  {prefix}.call_id: 存在 ✓ ({p_call_id[:40]})")

            elif btype == "message":
                # message: content[].text 存在 + 非空 + 无禁止 token
                v_contents = vi.get("content", [])
                p_contents = pi.get("content", [])
                if p_contents:
                    p_text = "".join(c.get("text", "") for c in p_contents
                                     if c.get("type") in ("output_text", "text"))
                    v_text = "".join(c.get("text", "") for c in v_contents
                                     if c.get("type") in ("output_text", "text"))
                    if "content" in extra_skip_paths:
                        infos.append(f"  {prefix}.content: 已配置跳过对比 ✓ (vllm_len={len(v_text)}, proxy_len={len(p_text)})")
                    elif p_text.strip():
                        infos.append(f"  {prefix}.content: 存在 ✓ (vllm_len={len(v_text)}, proxy_len={len(p_text)})")
                    else:
                        errors.append(f"{prefix}.content: proxy message 文本为空")
                    check_forbidden_tokens(p_text, f"{prefix}.content", errors, infos)
                else:
                    errors.append(f"{prefix}: proxy 缺失 message content")


def compare_responses_nonstream(vllm_data: dict, proxy_data: dict, label: str,
                                 extra_skip_paths: set = None) -> Tuple[list, list]:
    """对比 Responses /v1/responses 非流式响应"""
    errors, infos = [], []
    infos.append(f"[{label}] Responses 非流式对比")

    # 检测 error
    detect_nonstream_error(vllm_data, "vllm", errors)
    detect_nonstream_error(proxy_data, "proxy", errors)
    if errors:
        infos.append("  非流式响应包含 error, 跳过后续对比")
        return errors, infos

    # status 一致性
    v_status = vllm_data.get("status")
    p_status = proxy_data.get("status")
    compare_recursive(v_status, p_status, "status", errors, infos, RESPONSES_SKIP_FIELDS)

    # output[] 对比
    v_output = vllm_data.get("output", [])
    p_output = proxy_data.get("output", [])
    if not v_output:
        errors.append("vLLM 响应缺少 output 数组")
    if not p_output:
        errors.append("proxy 响应缺少 output 数组")
    if not v_output or not p_output:
        return errors, infos

    # output 数组长度对比（INFO 级别，转码层可导致差异）
    if len(v_output) != len(p_output):
        infos.append(f"  output: 数组长度不同 (vllm={len(v_output)}, proxy={len(p_output)}, 转换层差异)")

    _compare_responses_output(v_output, p_output, label, errors, infos, extra_skip_paths)

    # output_text 顶层字段（如果存在）
    v_output_text = vllm_data.get("output_text")
    p_output_text = proxy_data.get("output_text")
    if v_output_text is not None and p_output_text is not None:
        if v_output_text and p_output_text:
            infos.append(f"  output_text: 存在 ✓ (vllm_len={len(v_output_text)}, proxy_len={len(p_output_text)})")
        check_forbidden_tokens(p_output_text or "", "output_text", errors, infos)

    # usage 对比：仅验证 > 0（转码层可导致 token 计数差异）
    v_usage = vllm_data.get("usage")
    p_usage = proxy_data.get("usage")
    if v_usage and p_usage:
        _validate_usage_positive(v_usage, p_usage, "usage", errors, infos)

    return errors, infos


def parse_responses_sse(raw: str) -> list:
    """解析 Responses SSE 格式: event: xxx\\ndata: {...}\\n\\n

    与 Claude SSE 格式相同（event/data 交替），但无 [DONE] 标记。

    Returns:
        list of (event_name, event_data) tuples
    """
    events = []
    current_event = None
    current_data = ""

    for line in raw.strip().split("\n"):
        line_stripped = line.strip()
        if line_stripped.startswith("event: "):
            if current_event and current_data:
                try:
                    events.append((current_event, json.loads(current_data)))
                except json.JSONDecodeError:
                    pass
            current_event = line_stripped[7:].strip()
            current_data = ""
        elif line_stripped.startswith("data: "):
            current_data = line_stripped[6:].strip()
        elif line_stripped == "":
            if current_event and current_data:
                try:
                    events.append((current_event, json.loads(current_data)))
                except json.JSONDecodeError:
                    pass
                current_event = None
                current_data = ""

    # 末尾处理
    if current_event and current_data:
        try:
            events.append((current_event, json.loads(current_data)))
        except json.JSONDecodeError:
            pass

    return events


def _merge_responses_stream(events: list) -> list:
    """从 Responses SSE events 中合并出完整的 output[] 数组

    处理的事件类型:
    - response.output_item.added → 新增 output item
    - response.reasoning_summary_text.delta → 拼接 reasoning summary
    - response.output_text.delta → 拼接 message text
    - response.function_call_arguments.delta → 拼接 function_call arguments

    Returns:
        list of output item dicts (与合并后的非流式结构一致)
    """
    output_items = []
    current_item = None
    # 当前活跃的 content_part（message 的 content[] 元素）
    current_content_part = None

    for evt_name, evt_data in events:
        if evt_name == "response.output_item.added":
            item = evt_data.get("item") or evt_data.get("output_item") or {}
            if item:
                current_item = dict(item)
                # 确保必要字段存在
                if "content" not in current_item and current_item.get("type") == "message":
                    current_item["content"] = []
                if "summary" not in current_item and current_item.get("type") == "reasoning":
                    current_item["summary"] = []
                output_items.append(current_item)
            current_content_part = None

        elif evt_name == "response.content_part.added":
            # message 类型的 content_part 开始
            part = evt_data.get("part") or evt_data.get("content_part") or {}
            if current_item and current_item.get("type") == "message":
                if "content" not in current_item:
                    current_item["content"] = []
                current_content_part = dict(part)
                current_item["content"].append(current_content_part)

        elif evt_name == "response.reasoning_summary_text.delta":
            # reasoning summary 增量文本
            delta_text = evt_data.get("delta", "")
            if current_item and current_item.get("type") == "reasoning":
                if "summary" not in current_item:
                    current_item["summary"] = []
                # 追加到最后一个 summary 条目
                if not current_item["summary"]:
                    current_item["summary"].append({"type": "summary_text", "text": ""})
                last = current_item["summary"][-1]
                last["text"] = last.get("text", "") + delta_text

        elif evt_name == "response.reasoning_summary_part.done":
            # reasoning summary 部分完成，开启新部分
            part = evt_data.get("part") or {}
            if current_item and current_item.get("type") == "reasoning":
                if "summary" not in current_item:
                    current_item["summary"] = []
                current_item["summary"].append(dict(part))

        elif evt_name == "response.output_text.delta":
            # message text 增量
            delta_text = evt_data.get("delta", "")
            if current_content_part:
                current_content_part["text"] = current_content_part.get("text", "") + delta_text

        elif evt_name == "response.function_call_arguments.delta":
            # function_call arguments 增量
            delta = evt_data.get("delta", "")
            if current_item and current_item.get("type") == "function_call":
                current_item["arguments"] = current_item.get("arguments", "") + delta

        elif evt_name == "response.output_item.done":
            # item 完成，可能携带完整数据
            done_item = evt_data.get("item") or evt_data.get("output_item") or {}
            if done_item and current_item:
                # 用 done item 中更完整的字段覆盖（如 function_call 的 arguments）
                for key in ["arguments", "name", "call_id", "role"]:
                    if key in done_item:
                        current_item[key] = done_item[key]
            current_item = None
            current_content_part = None

    return output_items


def compare_responses_stream(vllm_raw: str, proxy_raw: str, label: str,
                              extra_skip_paths: set = None) -> Tuple[list, list]:
    """对比 Responses /v1/responses 流式响应"""
    errors, infos = [], []
    infos.append(f"[{label}] Responses 流式对比")

    v_events = parse_responses_sse(vllm_raw)
    p_events = parse_responses_sse(proxy_raw)

    # 检测 error 事件
    for source, events in [("vllm", v_events), ("proxy", p_events)]:
        for evt_name, evt_data in events:
            if evt_name == "error" or (isinstance(evt_data, dict) and "error" in evt_data
                                        and evt_data.get("type") != "response.completed"):
                err = evt_data.get("error", evt_data) if isinstance(evt_data, dict) else evt_data
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                errors.append(f"{source} 流式 error: {msg}")

    if errors:
        infos.append("  流式响应包含 error, 跳过后续对比")
        return errors, infos

    # 验证必要事件
    v_event_names = {e[0] for e in v_events}
    p_event_names = {e[0] for e in p_events}

    required_events = ["response.created", "response.completed"]
    for evt in required_events:
        if evt not in v_event_names:
            errors.append(f"vLLM 流式缺少 {evt} 事件")
        if evt not in p_event_names:
            errors.append(f"proxy 流式缺少 {evt} 事件")

    # 合并 delta 事件得到完整 output[]
    v_output = _merge_responses_stream(v_events)
    p_output = _merge_responses_stream(p_events)

    if not v_output:
        errors.append("vLLM 流式合并后无 output items")
    if not p_output:
        errors.append("proxy 流式合并后无 output items")
    if not v_output or not p_output:
        return errors, infos

    # 复用非流式的 output 对比逻辑
    _compare_responses_output(v_output, p_output, label, errors, infos, extra_skip_paths)

    # usage 对比：从 response.completed 事件中提取
    v_usage = {}
    p_usage = {}
    for evt_name, evt_data in v_events:
        if evt_name == "response.completed":
            resp = evt_data.get("response", {})
            v_usage = resp.get("usage", {})
            break
    for evt_name, evt_data in p_events:
        if evt_name == "response.completed":
            resp = evt_data.get("response", {})
            p_usage = resp.get("usage", {})
            break
    if v_usage and p_usage:
        _validate_usage_positive(v_usage, p_usage, "usage (stream)", errors, infos)

    return errors, infos


# ========================================
# 辅助函数
# ========================================

def compute_diff_stats(vllm_text: str, proxy_text: str) -> dict:
    """计算两个文本的差异统计"""
    vllm_len = len(vllm_text)
    proxy_len = len(proxy_text)
    vllm_words = vllm_text.split()
    proxy_words = proxy_text.split()
    sm = difflib.SequenceMatcher(None, vllm_words, proxy_words)
    return {
        "vllm_len": vllm_len,
        "proxy_len": proxy_len,
        "char_diff": abs(vllm_len - proxy_len),
        "word_similarity": sm.ratio(),
    }


# ========================================
# 主入口
# ========================================

def main():
    parser = argparse.ArgumentParser(description="trajproxy vs vLLM 响应对比工具")
    parser.add_argument("--mode", required=True, choices=["nonstream", "stream"],
                        help="对比模式")
    parser.add_argument("--api", default="openai", choices=["openai", "claude", "responses"],
                        help="API 格式")
    parser.add_argument("--vllm", required=True, help="vLLM 响应文件路径")
    parser.add_argument("--proxy", required=True, help="trajproxy 响应文件路径")
    parser.add_argument("--label", required=True, help="场景标签")
    parser.add_argument("--with-reasoning", action="store_true",
                        help="启用 reasoning 相关字段校验 (仅用于有 reasoning_parser 的场景)")
    parser.add_argument("--skip-paths", default="",
                        help="逗号分隔的路径子串列表，匹配的字段跳过值对比 (如 finish_reason,arguments)")

    args = parser.parse_args()

    with open(args.vllm, "r") as f:
        vllm_raw = f.read()
    with open(args.proxy, "r") as f:
        proxy_raw = f.read()

    if args.api == "openai":
        if args.mode == "nonstream":
            try:
                vllm_data = json.loads(vllm_raw.strip())
            except json.JSONDecodeError as e:
                print(f"FAIL:vLLM 响应 JSON 解析失败: {e}")
                sys.exit(1)
            try:
                proxy_data = json.loads(proxy_raw.strip())
            except json.JSONDecodeError as e:
                print(f"FAIL:proxy 响应 JSON 解析失败: {e}")
                sys.exit(1)
            extra_skip = {s.strip() for s in args.skip_paths.split(",") if s.strip()} if args.skip_paths else None
            errors, infos = compare_openai_nonstream(vllm_data, proxy_data, args.label,
                                                     with_reasoning=args.with_reasoning,
                                                     extra_skip_paths=extra_skip)
        else:
            extra_skip = {s.strip() for s in args.skip_paths.split(",") if s.strip()} if args.skip_paths else None
            errors, infos = compare_openai_stream(vllm_raw, proxy_raw, args.label,
                                                  extra_skip_paths=extra_skip)

    elif args.api == "claude":
        if args.mode == "nonstream":
            try:
                vllm_data = json.loads(vllm_raw.strip())
            except json.JSONDecodeError as e:
                print(f"FAIL:vLLM 响应 JSON 解析失败: {e}")
                sys.exit(1)
            try:
                proxy_data = json.loads(proxy_raw.strip())
            except json.JSONDecodeError as e:
                print(f"FAIL:proxy 响应 JSON 解析失败: {e}")
                sys.exit(1)
            errors, infos = compare_claude_nonstream(vllm_data, proxy_data, args.label)
        else:
            errors, infos = compare_claude_stream(vllm_raw, proxy_raw, args.label)

    elif args.api == "responses":
        extra_skip = {s.strip() for s in args.skip_paths.split(",") if s.strip()} if args.skip_paths else None
        if args.mode == "nonstream":
            try:
                vllm_data = json.loads(vllm_raw.strip())
            except json.JSONDecodeError as e:
                print(f"FAIL:vLLM 响应 JSON 解析失败: {e}")
                sys.exit(1)
            try:
                proxy_data = json.loads(proxy_raw.strip())
            except json.JSONDecodeError as e:
                print(f"FAIL:proxy 响应 JSON 解析失败: {e}")
                sys.exit(1)
            errors, infos = compare_responses_nonstream(vllm_data, proxy_data, args.label,
                                                         extra_skip_paths=extra_skip)
        else:
            errors, infos = compare_responses_stream(vllm_raw, proxy_raw, args.label,
                                                      extra_skip_paths=extra_skip)

    # 输出
    for line in infos:
        print(f"INFO:{line}")
    for err in errors:
        print(f"FAIL:{err}")

    if errors:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
