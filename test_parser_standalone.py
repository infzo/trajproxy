"""
独立的 parser 模块测试脚本
避免导入整个项目，只测试 parser 模块
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_base_module():
    """测试 base 模块"""
    print("=== Testing base module ===")

    from traj_proxy.proxy_core.parsers.base import (
        ToolCall,
        DeltaToolCall,
        ExtractedToolCallInfo,
        DeltaMessage,
        BaseToolParser,
        BaseReasoningParser,
        BaseParser,
    )

    # 测试数据结构
    tc = ToolCall(id="test_id", name="test_func", arguments='{"a": 1}')
    print(f"ToolCall: {tc}")

    dm = DeltaMessage(content="test content", reasoning="test reasoning")
    print(f"DeltaMessage: {dm}")

    print("base module: OK\n")


def test_tool_parser_manager():
    """测试 ToolParserManager"""
    print("=== Testing ToolParserManager ===")

    from traj_proxy.proxy_core.parsers.tool_parser_manager import ToolParserManager
    from traj_proxy.proxy_core.parsers.base import BaseToolParser, ExtractedToolCallInfo

    # 列出已注册的 parser
    parsers = ToolParserManager.list_registered()
    print(f"Registered tool parsers: {parsers}")

    # 测试懒加载注册
    ToolParserManager.register_lazy_module(
        name="test_parser",
        module_path="traj_proxy.proxy_core.parsers.tool_parsers.deepseekv3_tool_parser",
        class_name="DeepSeekV3ToolParser",
    )
    parsers = ToolParserManager.list_registered()
    print(f"After lazy register: {parsers}")

    # 获取 parser
    parser_cls = ToolParserManager.get_tool_parser("deepseek_v3")
    print(f"Got parser class: {parser_cls}")

    print("ToolParserManager: OK\n")


def test_reasoning_parser_manager():
    """测试 ReasoningParserManager"""
    print("=== Testing ReasoningParserManager ===")

    from traj_proxy.proxy_core.parsers.reasoning_parser_manager import ReasoningParserManager

    # 列出已注册的 parser
    parsers = ReasoningParserManager.list_registered()
    print(f"Registered reasoning parsers: {parsers}")

    # 获取 parser
    parser_cls = ReasoningParserManager.get_reasoning_parser("qwen3")
    print(f"Got parser class: {parser_cls}")

    print("ReasoningParserManager: OK\n")


def test_parser_manager():
    """测试 ParserManager"""
    print("=== Testing ParserManager ===")

    from traj_proxy.proxy_core.parsers.parser_manager import ParserManager

    # 注册模型配置
    ParserManager.register_model("deepseek-v3", {
        "tool_parser": "deepseek_v3",
        "reasoning_parser": "deepseek_v3",
    })
    ParserManager.register_model("qwen3-coder", {
        "tool_parser": "qwen3_coder",
        "reasoning_parser": "qwen3",
    })

    # 列出模型
    models = ParserManager.list_models()
    print(f"Registered models: {models}")

    # 获取模型配置
    config = ParserManager.get_model_config("deepseek-v3")
    print(f"deepseek-v3 config: {config}")

    print("ParserManager: OK\n")


def test_deepseek_v3_tool_parser():
    """测试 DeepSeek V3 工具解析器"""
    print("=== Testing DeepSeekV3ToolParser ===")

    from traj_proxy.proxy_core.parsers.tool_parser_manager import ToolParserManager

    parser_cls = ToolParserManager.get_tool_parser("deepseek_v3")
    parser = parser_cls()

    output = '''<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>get_weather
```json
{"city": "北京"}
```
<｜tool▁call▁end｜><｜tool▁calls▁end｜>'''

    result = parser.extract_tool_calls(output)
    print(f"tools_called: {result.tools_called}")
    print(f"tool_calls: {result.tool_calls}")
    print(f"content: {result.content}")

    assert result.tools_called == True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"

    print("DeepSeekV3ToolParser: OK\n")


def test_qwen3_reasoning_parser():
    """测试 Qwen3 推理解析器"""
    print("=== Testing Qwen3ReasoningParser ===")

    from traj_proxy.proxy_core.parsers.reasoning_parser_manager import ReasoningParserManager

    parser_cls = ReasoningParserManager.get_reasoning_parser("qwen3")
    parser = parser_cls()

    output = '<thinky>这是推理过程</thinke>这是正常回复'
    reasoning, content = parser.extract_reasoning(output)
    print(f"reasoning: {reasoning}")
    print(f"content: {content}")

    assert reasoning == "这是推理过程"
    assert content == "这是正常回复"

    print("Qwen3ReasoningParser: OK\n")


def test_unified_parser():
    """测试统一 Parser"""
    print("=== Testing Unified Parser ===")

    from traj_proxy.proxy_core.parsers.parser_manager import ParserManager

    # 创建统一 parser
    parser = ParserManager.create_parser_for_model("deepseek-v3")
    print(f"Created parser: {parser}")

    if parser:
        # 测试推理功能
        output = '<thinky>推理内容</thinke>正常回复'
        reasoning, content = parser.extract_reasoning(output)
        print(f"reasoning: {reasoning}")
        print(f"content: {content}")

    print("Unified Parser: OK\n")


def main():
    """运行所有测试"""
    try:
        test_base_module()
        test_tool_parser_manager()
        test_reasoning_parser_manager()
        test_parser_manager()
        test_deepseek_v3_tool_parser()
        test_qwen3_reasoning_parser()
        test_unified_parser()

        print("=" * 50)
        print("All tests passed!")
        print("=" * 50)

    except Exception as e:
        import traceback
        print(f"Test failed: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
