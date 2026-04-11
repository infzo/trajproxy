#!/usr/bin/env python3
"""
Jinja 模板多轮一致性校验脚本

校验 Jinja 模板是否满足：
1. 多轮一致性：第一轮输出是第二轮输出的子字符串
2. 首轮对话一致性：新模板首轮输出与旧模板完全一致

用法：
    python scripts/tools/verify_jinja_consistency.py \
        --original models/Qwen/Qwen3.5-2B/chat_template.jinja \
        --tito models/Qwen/Qwen3.5-2B/chat_template_tito.jinja
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("错误: jinja2 未安装。请运行: pip install jinja2")
    sys.exit(1)


# ==================== 测试用例定义 ====================

def get_test_cases():
    """获取所有测试用例"""
    return [
        # ========== 基础对话场景 ==========
        {
            "name": "纯对话-单轮",
            "messages_round1": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮助你的？"}
            ],
            "messages_round2": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
                {"role": "user", "content": "今天天气怎么样？"},
                {"role": "assistant", "content": "抱歉，我无法获取实时天气信息。"}
            ],
        },
        {
            "name": "纯对话-三轮",
            "messages_round1": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"}
            ],
            "messages_round2": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
                {"role": "user", "content": "再见"},
                {"role": "assistant", "content": "再见！"}
            ],
            "messages_round3": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
                {"role": "user", "content": "再见"},
                {"role": "assistant", "content": "再见！"},
                {"role": "user", "content": "又见面了"},
                {"role": "assistant", "content": "是的！"}
            ],
        },
        # ========== Tool Calls 场景 ==========
        {
            "name": "Tool调用-单次",
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "获取城市天气",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名称"}
                        },
                        "required": ["city"]
                    }
                }
            }],
            "messages_round1": [
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "北京"}}}
                ]},
                {"role": "tool", "content": "晴天 25度"},
                {"role": "assistant", "content": "北京今天晴天，25度"}
            ],
            "messages_round2": [
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "北京"}}}
                ]},
                {"role": "tool", "content": "晴天 25度"},
                {"role": "assistant", "content": "北京今天晴天，25度"},
                {"role": "user", "content": "上海呢"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "上海"}}}
                ]},
                {"role": "tool", "content": "多云 20度"},
                {"role": "assistant", "content": "上海多云，20度"}
            ],
        },
        {
            "name": "Tool调用-连续多轮",
            "tools": [{
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "搜索信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"}
                        },
                        "required": ["query"]
                    }
                }
            }],
            "messages_round1": [
                {"role": "user", "content": "搜索Python教程"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search", "arguments": {"query": "Python教程"}}}
                ]},
                {"role": "tool", "content": "找到多个Python教程..."},
                {"role": "assistant", "content": "找到了多个Python教程，需要详细介绍吗？"}
            ],
            "messages_round2": [
                {"role": "user", "content": "搜索Python教程"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search", "arguments": {"query": "Python教程"}}}
                ]},
                {"role": "tool", "content": "找到多个Python教程..."},
                {"role": "assistant", "content": "找到了多个Python教程，需要详细介绍吗？"},
                {"role": "user", "content": "搜索JavaScript教程"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search", "arguments": {"query": "JavaScript教程"}}}
                ]},
                {"role": "tool", "content": "找到多个JavaScript教程..."},
                {"role": "assistant", "content": "找到了多个JavaScript教程。"}
            ],
            "messages_round3": [
                {"role": "user", "content": "搜索Python教程"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search", "arguments": {"query": "Python教程"}}}
                ]},
                {"role": "tool", "content": "找到多个Python教程..."},
                {"role": "assistant", "content": "找到了多个Python教程，需要详细介绍吗？"},
                {"role": "user", "content": "搜索JavaScript教程"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search", "arguments": {"query": "JavaScript教程"}}}
                ]},
                {"role": "tool", "content": "找到多个JavaScript教程..."},
                {"role": "assistant", "content": "找到了多个JavaScript教程。"},
                {"role": "user", "content": "搜索Rust教程"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "search", "arguments": {"query": "Rust教程"}}}
                ]},
                {"role": "tool", "content": "找到多个Rust教程..."},
                {"role": "assistant", "content": "找到了多个Rust教程。"}
            ],
        },
        {
            "name": "Tool调用-多工具连续",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "获取天气",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string", "description": "城市"}
                            },
                            "required": ["city"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_time",
                        "description": "获取时间",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "timezone": {"type": "string", "description": "时区"}
                            },
                            "required": ["timezone"]
                        }
                    }
                }
            ],
            "messages_round1": [
                {"role": "user", "content": "北京天气和时间"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "北京"}}},
                    {"function": {"name": "get_time", "arguments": {"timezone": "Asia/Shanghai"}}}
                ]},
                {"role": "tool", "content": "晴天"},
                {"role": "tool", "content": "14:30"},
                {"role": "assistant", "content": "北京天气晴天，现在是14:30"}
            ],
            "messages_round2": [
                {"role": "user", "content": "北京天气和时间"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "北京"}}},
                    {"function": {"name": "get_time", "arguments": {"timezone": "Asia/Shanghai"}}}
                ]},
                {"role": "tool", "content": "晴天"},
                {"role": "tool", "content": "14:30"},
                {"role": "assistant", "content": "北京天气晴天，现在是14:30"},
                {"role": "user", "content": "上海呢"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "上海"}}},
                    {"function": {"name": "get_time", "arguments": {"timezone": "Asia/Shanghai"}}}
                ]},
                {"role": "tool", "content": "多云"},
                {"role": "tool", "content": "14:35"},
                {"role": "assistant", "content": "上海多云，现在是14:35"}
            ],
        },
        # ========== Reasoning Content 场景 ==========
        {
            "name": "Reasoning内容-基础",
            "messages_round1": [
                {"role": "user", "content": "1+1=?"},
                {"role": "assistant", "reasoning_content": "这是一个简单的加法问题", "content": "答案是2"}
            ],
            "messages_round2": [
                {"role": "user", "content": "1+1=?"},
                {"role": "assistant", "reasoning_content": "这是一个简单的加法问题", "content": "答案是2"},
                {"role": "user", "content": "那2+2呢？"},
                {"role": "assistant", "reasoning_content": "继续加法运算", "content": "答案是4"}
            ],
        },
        {
            "name": "Reasoning内容-多轮",
            "messages_round1": [
                {"role": "user", "content": "计算123+456"},
                {"role": "assistant", "reasoning_content": "需要计算两个三位数的和\n123 + 456\n个位: 3+6=9\n十位: 2+5=7\n百位: 1+4=5\n结果: 579", "content": "123+456=579"}
            ],
            "messages_round2": [
                {"role": "user", "content": "计算123+456"},
                {"role": "assistant", "reasoning_content": "需要计算两个三位数的和\n123 + 456\n个位: 3+6=9\n十位: 2+5=7\n百位: 1+4=5\n结果: 579", "content": "123+456=579"},
                {"role": "user", "content": "再计算789+123"},
                {"role": "assistant", "reasoning_content": "继续加法计算\n789 + 123\n个位: 9+3=12，进位1\n十位: 8+2+1=11，进位1\n百位: 7+1+1=9\n结果: 912", "content": "789+123=912"}
            ],
        },
        # ========== 混合场景 ==========
        {
            "name": "混合-Tool+Reasoning",
            "messages_round1": [
                {"role": "user", "content": "帮我查天气"},
                {"role": "assistant", "reasoning_content": "用户想查天气，需要调用天气API", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "北京"}}}
                ]},
                {"role": "tool", "content": "晴天"},
                {"role": "assistant", "content": "北京今天是晴天"}
            ],
            "messages_round2": [
                {"role": "user", "content": "帮我查天气"},
                {"role": "assistant", "reasoning_content": "用户想查天气，需要调用天气API", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "北京"}}}
                ]},
                {"role": "tool", "content": "晴天"},
                {"role": "assistant", "content": "北京今天是晴天"},
                {"role": "user", "content": "上海呢"},
                {"role": "assistant", "reasoning_content": "用户问上海天气", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "上海"}}}
                ]},
                {"role": "tool", "content": "多云"},
                {"role": "assistant", "content": "上海多云"}
            ],
        },
        {
            "name": "混合-对话后Tool调用",
            "tools": [{
                "type": "function",
                "function": {
                    "name": "calculate",
                    "description": "计算表达式",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string", "description": "数学表达式"}
                        },
                        "required": ["expression"]
                    }
                }
            }],
            "messages_round1": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮助你的？"}
            ],
            "messages_round2": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
                {"role": "user", "content": "帮我计算123*456"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "calculate", "arguments": {"expression": "123*456"}}}
                ]},
                {"role": "tool", "content": "56088"},
                {"role": "assistant", "content": "123乘以456等于56088"}
            ],
        },
        # ========== 复杂场景 ==========
        {
            "name": "复杂-系统提示+Tool",
            "tools": [{
                "type": "function",
                "function": {
                    "name": "translate",
                    "description": "翻译文本",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "要翻译的文本"},
                            "target_lang": {"type": "string", "description": "目标语言"}
                        },
                        "required": ["text", "target_lang"]
                    }
                }
            }],
            "messages_round1": [
                {"role": "system", "content": "你是一个专业的翻译助手。"},
                {"role": "user", "content": "将'Hello World'翻译成中文"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "translate", "arguments": {"text": "Hello World", "target_lang": "Chinese"}}}
                ]},
                {"role": "tool", "content": "你好世界"},
                {"role": "assistant", "content": "翻译结果：你好世界"}
            ],
            "messages_round2": [
                {"role": "system", "content": "你是一个专业的翻译助手。"},
                {"role": "user", "content": "将'Hello World'翻译成中文"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "translate", "arguments": {"text": "Hello World", "target_lang": "Chinese"}}}
                ]},
                {"role": "tool", "content": "你好世界"},
                {"role": "assistant", "content": "翻译结果：你好世界"},
                {"role": "user", "content": "再翻译成日语"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "translate", "arguments": {"text": "Hello World", "target_lang": "Japanese"}}}
                ]},
                {"role": "tool", "content": "こんにちは世界"},
                {"role": "assistant", "content": "日语翻译：こんにちは世界"}
            ],
        },
        {
            "name": "复杂-长对话",
            "messages_round1": [
                {"role": "user", "content": "请介绍一下Python"},
                {"role": "assistant", "content": "Python是一种高级编程语言，以其简洁易读的语法著称。"}
            ],
            "messages_round2": [
                {"role": "user", "content": "请介绍一下Python"},
                {"role": "assistant", "content": "Python是一种高级编程语言，以其简洁易读的语法著称。"},
                {"role": "user", "content": "它的主要特点是什么？"},
                {"role": "assistant", "content": "Python的主要特点包括：简洁易读、跨平台、丰富的库生态、动态类型、自动内存管理等。"}
            ],
            "messages_round3": [
                {"role": "user", "content": "请介绍一下Python"},
                {"role": "assistant", "content": "Python是一种高级编程语言，以其简洁易读的语法著称。"},
                {"role": "user", "content": "它的主要特点是什么？"},
                {"role": "assistant", "content": "Python的主要特点包括：简洁易读、跨平台、丰富的库生态、动态类型、自动内存管理等。"},
                {"role": "user", "content": "适合什么场景？"},
                {"role": "assistant", "content": "Python适合Web开发、数据分析、人工智能、自动化脚本、科学计算等场景。"}
            ],
        },
    ]


# ==================== 校验函数 ====================

def load_template(template_path: str):
    """加载模板"""
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")

    template_dir = path.parent
    template_name = path.name

    env = Environment(loader=FileSystemLoader(template_dir))
    return env.get_template(template_name)


def render_template(template, messages, tools=None, add_generation_prompt=False, **kwargs):
    """渲染模板"""
    return template.render(
        messages=messages,
        tools=tools,
        add_generation_prompt=add_generation_prompt,
        **kwargs
    )


def verify_multiround_consistency(template, test_case: dict) -> tuple[bool, str]:
    """
    验证多轮一致性

    Returns:
        (是否通过, 错误信息)
    """
    messages_round1 = test_case["messages_round1"]
    messages_round2 = test_case.get("messages_round2")
    messages_round3 = test_case.get("messages_round3")
    tools = test_case.get("tools")

    name = test_case["name"]

    # 渲染第一轮
    output1 = render_template(template, messages_round1, tools=tools, add_generation_prompt=False)

    if messages_round2:
        # 渲染第二轮
        output2 = render_template(template, messages_round2, tools=tools, add_generation_prompt=False)

        # 验证第一轮在第二轮中
        if output1 not in output2:
            return False, (
                f"多轮一致性失败:\n"
                f"  第一轮输出({len(output1)}字符)不是第二轮输出({len(output2)}字符)的子字符串\n"
                f"  第一轮输出:\n{output1[:500]}...\n\n"
                f"  第二轮输出:\n{output2[:500]}..."
            )

    if messages_round3:
        # 渲染第三轮
        output3 = render_template(template, messages_round3, tools=tools, add_generation_prompt=False)

        # 验证第二轮在第三轮中
        if messages_round2:
            output2 = render_template(template, messages_round2, tools=tools, add_generation_prompt=False)
            if output2 not in output3:
                return False, (
                    f"多轮一致性失败(第二轮到第三轮):\n"
                    f"  第二轮输出({len(output2)}字符)不是第三轮输出({len(output3)}字符)的子字符串"
                )

        # 验证第一轮在第三轮中（传递性）
        if output1 not in output3:
            return False, (
                f"多轮一致性失败(传递性):\n"
                f"  第一轮输出({len(output1)}字符)不是第三轮输出({len(output3)}字符)的子字符串"
            )

    return True, ""


def verify_first_round_consistency(
    original_template,
    consistency_template,
    test_case: dict
) -> tuple[bool, str]:
    """
    验证首轮对话一致性

    Returns:
        (是否通过, 错误信息)
    """
    messages_round1 = test_case["messages_round1"]
    tools = test_case.get("tools")
    name = test_case["name"]

    output_original = render_template(original_template, messages_round1, tools=tools, add_generation_prompt=False)
    output_consistency = render_template(consistency_template, messages_round1, tools=tools, add_generation_prompt=False)

    if output_original != output_consistency:
        return False, (
            f"首轮对话不一致:\n"
            f"  原始输出长度: {len(output_original)}\n"
            f"  一致性输出长度: {len(output_consistency)}\n"
            f"  原始输出:\n{output_original}\n\n"
            f"  一致性输出:\n{output_consistency}"
        )

    return True, ""


def run_verification(original_path: str, consistency_path: str, verbose: bool = False) -> bool:
    """
    运行完整校验

    Args:
        original_path: 原始模板路径
        consistency_path: 一致性模板路径
        verbose: 是否显示详细输出

    Returns:
        是否全部通过
    """
    print("=" * 70)
    print("Jinja 模板多轮一致性校验")
    print("=" * 70)
    print(f"原始模板: {original_path}")
    print(f"一致性模板: {consistency_path}")
    print()

    # 加载模板
    try:
        original_template = load_template(original_path)
        consistency_template = load_template(consistency_path)
    except FileNotFoundError as e:
        print(f"✗ 错误: {e}")
        return False

    test_cases = get_test_cases()
    total = len(test_cases)

    # 统计
    multiround_passed = 0
    first_round_passed = 0
    failed_cases = []

    # 1. 多轮一致性校验
    print("-" * 70)
    print("1. 多轮一致性校验")
    print("-" * 70)

    for case in test_cases:
        passed, error = verify_multiround_consistency(consistency_template, case)
        if passed:
            multiround_passed += 1
            print(f"  ✓ {case['name']}")
        else:
            print(f"  ✗ {case['name']}")
            if verbose:
                print(f"    {error[:200]}...")
            failed_cases.append(("多轮一致性", case['name'], error))

    print(f"\n多轮一致性: {multiround_passed}/{total} 通过")

    # 2. 首轮对话一致性校验
    print()
    print("-" * 70)
    print("2. 首轮对话一致性校验")
    print("-" * 70)

    for case in test_cases:
        passed, error = verify_first_round_consistency(original_template, consistency_template, case)
        if passed:
            first_round_passed += 1
            print(f"  ✓ {case['name']}")
        else:
            print(f"  ✗ {case['name']}")
            if verbose:
                print(f"    {error[:200]}...")
            failed_cases.append(("首轮一致性", case['name'], error))

    print(f"\n首轮一致性: {first_round_passed}/{total} 通过")

    # 3. 汇总
    print()
    print("=" * 70)
    print("校验汇总")
    print("=" * 70)
    print(f"  多轮一致性: {multiround_passed}/{total} 通过")
    print(f"  首轮一致性: {first_round_passed}/{total} 通过")

    if failed_cases:
        print(f"\n失败用例: {len(failed_cases)} 个")
        if verbose:
            for check_type, name, error in failed_cases:
                print(f"\n[{check_type}] {name}:")
                print(error)
        return False
    else:
        print("\n✓ 全部校验通过!")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Jinja 模板多轮一致性校验脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法
  python scripts/tools/verify_jinja_consistency.py \\
      --original models/Qwen/Qwen3.5-2B/chat_template.jinja \\
      --tito models/Qwen/Qwen3.5-2B/chat_template_tito.jinja

  # 显示详细错误信息
  python scripts/tools/verify_jinja_consistency.py \\
      --original models/Qwen/Qwen3.5-2B/chat_template.jinja \\
      --tito models/Qwen/Qwen3.5-2B/chat_template_tito.jinja \\
      --verbose
        """
    )

    parser.add_argument(
        "--original",
        required=True,
        help="原始 Jinja 模板路径"
    )
    parser.add_argument(
        "--tito",
        required=True,
        help="TITO 一致性 Jinja 模板路径"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细输出"
    )

    args = parser.parse_args()

    success = run_verification(args.original, args.tito, args.verbose)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()