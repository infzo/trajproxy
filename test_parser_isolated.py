"""
独立的 parser 模块测试脚本
测试核心解析器功能
"""

import sys
import os
import json

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_base_dataclasses():
    """测试 base 模块中的数据结构"""
    print("=== Testing base dataclasses ===")

    # 直接从文件读取并执行
    base_code = open('traj_proxy/proxy_core/parsers/base.py').read()

    # 移除相对导入
    base_code = base_code.replace('from .base import', 'from __main__ import')
    base_code = base_code.replace('from ..base import', 'from __main__ import')

    # 执行代码
    exec_globals = {}
    exec(base_code, exec_globals)

    # 测试数据结构
    ToolCall = exec_globals['ToolCall']
    DeltaMessage = exec_globals['DeltaMessage']
    ExtractedToolCallInfo = exec_globals['ExtractedToolCallInfo']

    tc = ToolCall(id="test_id", name="test_func", arguments='{"a": 1}')
    print(f"ToolCall: {tc}")

    dm = DeltaMessage(content="test content", reasoning="test reasoning")
    print(f"DeltaMessage: {dm}")

    print("base dataclasses: OK\n")
    return exec_globals


def test_deepseek_v3_parser():
    """测试 DeepSeek V3 工具解析器"""
    print("=== Testing DeepSeekV3ToolParser ===")

    # 获取 base 模块
    exec_globals = test_base_dataclasses()

    # 执行 deepseekv3_tool_parser.py
    parser_code = open('traj_proxy/proxy_core/parsers/tool_parsers/deepseekv3_tool_parser.py').read()
    parser_code = parser_code.replace('from ..base import', '')
    exec(parser_code, exec_globals)

    DeepSeekV3ToolParser = exec_globals['DeepSeekV3ToolParser']
    ExtractedToolCallInfo = exec_globals['ExtractedToolCallInfo']

    parser = DeepSeekV3ToolParser()

    output = '''<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>get_weather
```json
{"city": "北京"}
```
<｜tool▁call▁end｜><｜tool▁calls▁end｜>'''

    result = parser.extract_tool_calls(output)
    print(f"tools_called: {result.tools_called}")
    print(f"tool_calls: {result.tool_calls}")

    assert result.tools_called == True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"

    args = json.loads(result.tool_calls[0].arguments)
    assert args["city"] == "北京"

    print("DeepSeekV3ToolParser: OK\n")


def test_qwen3_reasoning_parser():
    """测试 Qwen3 推理解析器"""
    print("=== Testing Qwen3ReasoningParser ===")

    # 获取 base 模块
    exec_globals = test_base_dataclasses()

    # 执行 basic_parsers.py
    basic_code = open('traj_proxy/proxy_core/parsers/reasoning_parsers/basic_parsers.py').read()
    basic_code = basic_code.replace('from ..base import', '')
    exec(basic_code, exec_globals)

    # 执行 qwen3_reasoning_parser.py
    parser_code = open('traj_proxy/proxy_core/parsers/reasoning_parsers/qwen3_reasoning_parser.py').read()
    parser_code = parser_code.replace('from .basic_parsers import', '')
    exec(parser_code, exec_globals)

    Qwen3ReasoningParser = exec_globals['Qwen3ReasoningParser']

    parser = Qwen3ReasoningParser()

    output = '<thinky>这是推理过程</thinke>这是正常回复'
    reasoning, content = parser.extract_reasoning(output)
    print(f"reasoning: {reasoning}")
    print(f"content: {content}")

    assert reasoning == "这是推理过程"
    assert content == "这是正常回复"

    # 测试无 token 的情况
    output2 = '这是普通回复'
    reasoning2, content2 = parser.extract_reasoning(output2)
    print(f"reasoning (no token): {reasoning2}")
    print(f"content (no token): {content2}")

    assert reasoning2 is None
    assert content2 == "这是普通回复"

    print("Qwen3ReasoningParser: OK\n")


def test_deepseek_v32_parser():
    """测试 DeepSeek V3.2 (DSML) 工具解析器"""
    print("=== Testing DeepSeekV32ToolParser ===")

    # 获取 base 模块
    exec_globals = test_base_dataclasses()

    # 执行 deepseekv32_tool_parser.py
    parser_code = open('traj_proxy/proxy_core/parsers/tool_parsers/deepseekv32_tool_parser.py').read()
    parser_code = parser_code.replace('from ..base import', '')
    exec(parser_code, exec_globals)

    DeepSeekV32ToolParser = exec_globals['DeepSeekV32ToolParser']

    parser = DeepSeekV32ToolParser()

    output = '''<｜DSML｜function_calls>
<｜DSML｜invoke name="get_weather">
<｜DSML｜parameter name="location" string="true">杭州</｜DSML｜parameter>
<｜DSML｜parameter name="date" string="true">2024-01-16</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜function_calls>'''

    result = parser.extract_tool_calls(output)
    print(f"tools_called: {result.tools_called}")
    print(f"tool_calls: {result.tool_calls}")

    assert result.tools_called == True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"

    args = json.loads(result.tool_calls[0].arguments)
    assert args["location"] == "杭州"
    assert args["date"] == "2024-01-16"

    print("DeepSeekV32ToolParser: OK\n")


def test_qwen3_coder_parser():
    """测试 Qwen3 Coder 工具解析器"""
    print("=== Testing Qwen3CoderToolParser ===")

    # 获取 base 模块
    exec_globals = test_base_dataclasses()

    # 执行 qwen3coder_tool_parser.py
    parser_code = open('traj_proxy/proxy_core/parsers/tool_parsers/qwen3coder_tool_parser.py').read()
    parser_code = parser_code.replace('from ..base import', '')
    exec(parser_code, exec_globals)

    Qwen3CoderToolParser = exec_globals['Qwen3CoderToolParser']

    parser = Qwen3CoderToolParser()

    output = '''toral<function=get_weather>
<parameter=city>北京</parameter>
<parameter=unit>celsius</parameter>
</function> Ranchi'''

    result = parser.extract_tool_calls(output)
    print(f"tools_called: {result.tools_called}")
    print(f"tool_calls: {result.tool_calls}")

    assert result.tools_called == True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"

    args = json.loads(result.tool_calls[0].arguments)
    assert args["city"] == "北京"
    assert args["unit"] == "celsius"

    print("Qwen3CoderToolParser: OK\n")


def main():
    """运行所有测试"""
    try:
        test_deepseek_v3_parser()
        test_qwen3_reasoning_parser()
        test_deepseek_v32_parser()
        test_qwen3_coder_parser()

        print("=" * 50)
        print("All tests passed!")
        print("=" * 50)

    except Exception as e:
        import traceback
        print(f"Test failed: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
