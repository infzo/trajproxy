"""
Parser 模块单元测试
测试重构后的 Parser 模块，验证默认行为与 vLLM 一致
"""

import json
import pytest

from traj_proxy.proxy_core.parsers import (
    ParserManager,
    ToolParserManager,
    ReasoningParserManager,
    Parser,
    IdentityParser,
    BaseToolParser,
    BaseReasoningParser,
)
from traj_proxy.proxy_core.parsers.base import (
    ToolCall, DeltaToolCall, ExtractedToolCallInfo, DeltaMessage
)


class TestDefaultBehavior:
    """测试默认行为（与 vLLM 一致）"""

    def test_get_parser_empty_returns_none(self):
        """测试：不指定 parser 时返回 None"""
        parser_cls = ParserManager.get_parser(
            tool_parser_name="",
            reasoning_parser_name="",
        )
        assert parser_cls is None

    def test_get_parser_none_returns_none(self):
        """测试：参数为 None 时返回 None"""
        parser_cls = ParserManager.get_parser(
            tool_parser_name=None,
            reasoning_parser_name=None,
        )
        assert parser_cls is None

    def test_create_parser_empty_returns_none(self):
        """测试：不指定 parser 时返回 None"""
        parser = ParserManager.create_parser(
            tool_parser_name="",
            reasoning_parser_name="",
        )
        assert parser is None

    def test_create_parsers_empty_returns_none(self):
        """测试：不指定 parser 时返回 (None, None)"""
        tool_parser, reasoning_parser = ParserManager.create_parsers()
        assert tool_parser is None
        assert reasoning_parser is None

    def test_identity_parser_no_parsing(self):
        """测试：IdentityParser 不做解析"""
        parser = IdentityParser()

        # 测试 reasoning
        output = "这是普通回复"
        reasoning, content = parser.extract_reasoning(output)
        assert reasoning is None
        assert content == output

        # 测试 tool calls
        result = parser.extract_tool_calls(output)
        assert result.tools_called == False
        assert len(result.tool_calls) == 0
        assert result.content == output

    def test_identity_parser_streaming(self):
        """测试：IdentityParser 流式不做解析"""
        parser = IdentityParser()

        # 测试 reasoning streaming
        delta = parser.extract_reasoning_streaming(
            previous_text="",
            current_text="测试",
            delta_text="测试",
            previous_token_ids=[],
            current_token_ids=[1, 2],
            delta_token_ids=[1, 2],
        )
        assert delta.content == "测试"

        # 测试 tool calls streaming
        delta = parser.extract_tool_calls_streaming(
            previous_text="",
            current_text="测试",
            delta_text="测试",
            previous_token_ids=[],
            current_token_ids=[1, 2],
            delta_token_ids=[1, 2],
        )
        assert delta.content == "测试"


class TestToolParserManager:
    """ToolParserManager 测试"""

    def test_list_registered(self):
        """测试列出已注册的 parser"""
        parsers = ToolParserManager.list_registered()
        assert "deepseek_v3" in parsers
        assert "deepseek_v31" in parsers
        assert "deepseek_v32" in parsers
        assert "qwen3_coder" in parsers
        assert "glm45" in parsers
        assert "glm47" in parsers

    def test_get_tool_parser(self):
        """测试获取 parser 类"""
        parser_cls = ToolParserManager.get_tool_parser("deepseek_v3")
        assert parser_cls is not None
        assert issubclass(parser_cls, BaseToolParser)

    def test_get_nonexistent_parser(self):
        """测试获取不存在的 parser"""
        with pytest.raises(KeyError):
            ToolParserManager.get_tool_parser("nonexistent")


class TestReasoningParserManager:
    """ReasoningParserManager 测试"""

    def test_list_registered(self):
        """测试列出已注册的 parser"""
        parsers = ReasoningParserManager.list_registered()
        assert "deepseek_r1" in parsers
        assert "deepseek_v3" in parsers
        assert "qwen3" in parsers
        assert "glm45" in parsers

    def test_get_reasoning_parser(self):
        """测试获取 parser 类"""
        parser_cls = ReasoningParserManager.get_reasoning_parser("qwen3")
        assert parser_cls is not None
        assert issubclass(parser_cls, BaseReasoningParser)


class TestParserManager:
    """ParserManager 测试"""

    def test_list_tool_parsers(self):
        """测试列出 tool parsers"""
        parsers = ParserManager.list_tool_parsers()
        assert "deepseek_v3" in parsers
        assert "qwen3_coder" in parsers

    def test_list_reasoning_parsers(self):
        """测试列出 reasoning parsers"""
        parsers = ParserManager.list_reasoning_parsers()
        assert "qwen3" in parsers
        assert "deepseek_r1" in parsers

    def test_get_parser_with_names(self):
        """测试指定名称获取 Parser"""
        parser_cls = ParserManager.get_parser(
            tool_parser_name="deepseek_v3",
            reasoning_parser_name="deepseek_v3",
        )
        assert parser_cls is not None
        assert issubclass(parser_cls, Parser)

    def test_create_parsers_with_names(self):
        """测试指定名称创建 Parser 实例"""
        tool_parser, reasoning_parser = ParserManager.create_parsers(
            tool_parser_name="deepseek_v3",
            reasoning_parser_name="deepseek_v3",
        )
        assert tool_parser is not None
        assert reasoning_parser is not None

    def test_create_parsers_only_tool(self):
        """测试只指定 tool parser"""
        tool_parser, reasoning_parser = ParserManager.create_parsers(
            tool_parser_name="deepseek_v3",
        )
        assert tool_parser is not None
        assert reasoning_parser is None

    def test_create_parsers_only_reasoning(self):
        """测试只指定 reasoning parser"""
        tool_parser, reasoning_parser = ParserManager.create_parsers(
            reasoning_parser_name="qwen3",
        )
        assert tool_parser is None
        assert reasoning_parser is not None


class TestDeepSeekV3ToolParser:
    """DeepSeek V3 工具解析器测试"""

    def test_extract_tool_calls_single(self):
        """测试单个工具调用解析"""
        parser_cls = ToolParserManager.get_tool_parser("deepseek_v3")
        parser = parser_cls()

        output = '''<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>get_weather
```json
{"city": "北京"}
```
<｜tool▁call▁end｜><｜tool▁calls▁end｜>'''

        result = parser.extract_tool_calls(output)

        assert result.tools_called == True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"

        args = json.loads(result.tool_calls[0].arguments)
        assert args["city"] == "北京"

    def test_extract_tool_calls_no_tools(self):
        """测试无工具调用的情况"""
        parser_cls = ToolParserManager.get_tool_parser("deepseek_v3")
        parser = parser_cls()

        output = "这是一个普通的回复"

        result = parser.extract_tool_calls(output)

        assert result.tools_called == False
        assert result.content == output


class TestDeepSeekV32ToolParser:
    """DeepSeek V3.2 (DSML) 工具解析器测试"""

    def test_extract_tool_calls_single(self):
        """测试单个工具调用解析"""
        parser_cls = ToolParserManager.get_tool_parser("deepseek_v32")
        parser = parser_cls()

        output = '''<｜DSML｜function_calls>
<｜DSML｜invoke name="get_weather">
<｜DSML｜parameter name="location" string="true">杭州</｜DSML｜parameter>
<｜DSML｜parameter name="date" string="true">2024-01-16</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜function_calls>'''

        result = parser.extract_tool_calls(output)

        assert result.tools_called == True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"

        args = json.loads(result.tool_calls[0].arguments)
        assert args["location"] == "杭州"
        assert args["date"] == "2024-01-16"


class TestQwen3CoderToolParser:
    """Qwen3 Coder 工具解析器测试"""

    def test_extract_tool_calls_single(self):
        """测试单个工具调用解析"""
        parser_cls = ToolParserManager.get_tool_parser("qwen3_coder")
        parser = parser_cls()

        output = '''toral<function=get_weather>
<parameter=city>北京</parameter>
<parameter=unit>celsius</parameter>
</function> Ranchi'''

        result = parser.extract_tool_calls(output)

        assert result.tools_called == True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"

        args = json.loads(result.tool_calls[0].arguments)
        assert args["city"] == "北京"
        assert args["unit"] == "celsius"

    def test_extract_tool_calls_with_type_conversion(self):
        """测试参数类型转换"""
        parser_cls = ToolParserManager.get_tool_parser("qwen3_coder")
        parser = parser_cls()

        output = '''toral<function=calculate>
<parameter=num>42</parameter>
<parameter=pi>3.14</parameter>
</function> Ranchi'''

        tools = [{
            "type": "function",
            "function": {
                "name": "calculate",
                "parameters": {
                    "properties": {
                        "num": {"type": "integer"},
                        "pi": {"type": "number"}
                    }
                }
            }
        }]

        result = parser.extract_tool_calls(output, tools=tools)

        assert result.tools_called == True
        args = json.loads(result.tool_calls[0].arguments)
        assert args["num"] == 42
        assert args["pi"] == 3.14


class TestQwen3ReasoningParser:
    """Qwen3 推理解析器测试"""

    def test_extract_reasoning(self):
        """测试推理内容解析"""
        parser_cls = ReasoningParserManager.get_reasoning_parser("qwen3")
        parser = parser_cls()

        output = '<thinky>这是推理过程</thinke>这是正常回复'

        reasoning, content = parser.extract_reasoning(output)

        assert reasoning == "这是推理过程"
        assert content == "这是正常回复"

    def test_extract_reasoning_no_tokens(self):
        """测试没有推理标记的情况"""
        parser_cls = ReasoningParserManager.get_reasoning_parser("qwen3")
        parser = parser_cls()

        output = '这是普通回复'

        reasoning, content = parser.extract_reasoning(output)

        assert reasoning is None
        assert content == "这是普通回复"


class TestDeepSeekR1ReasoningParser:
    """DeepSeek R1 推理解析器测试"""

    def test_extract_reasoning(self):
        """测试推理内容解析"""
        parser_cls = ReasoningParserManager.get_reasoning_parser("deepseek_r1")
        parser = parser_cls()

        output = '<thinky>这是推理过程</thinke>这是正常回复'

        reasoning, content = parser.extract_reasoning(output)

        assert reasoning == "这是推理过程"
        assert content == "这是正常回复"


class TestDeepSeekV3ReasoningParser:
    """DeepSeek V3 推理解析器测试"""

    def test_extract_reasoning_with_thinking(self):
        """测试启用 thinking 模式"""
        parser_cls = ReasoningParserManager.get_reasoning_parser("deepseek_v3")
        parser = parser_cls(chat_template_kwargs={"thinking": True})

        output = '<thinky>这是推理过程</thinke>这是正常回复'

        reasoning, content = parser.extract_reasoning(output)

        assert reasoning == "这是推理过程"
        assert content == "这是正常回复"

    def test_extract_reasoning_without_thinking(self):
        """测试禁用 thinking 模式"""
        parser_cls = ReasoningParserManager.get_reasoning_parser("deepseek_v3")
        parser = parser_cls(chat_template_kwargs={"thinking": False})

        output = '这是普通回复'

        reasoning, content = parser.extract_reasoning(output)

        assert reasoning is None
        assert content == "这是普通回复"


class TestUnifiedParser:
    """统一 Parser 测试"""

    def test_create_parser_with_config(self):
        """测试为指定 parser 名称创建 Parser"""
        parser = ParserManager.create_parser(
            tool_parser_name="deepseek_v3",
            reasoning_parser_name="deepseek_v3",
        )
        assert parser is not None
        assert not isinstance(parser, IdentityParser)

        # 测试推理功能
        output = '<thinky>推理内容</thinke>正常回复'
        reasoning, content = parser.extract_reasoning(output)
        assert reasoning == "推理内容"

    def test_create_parser_without_config(self):
        """测试不指定 parser 时返回 None"""
        parser = ParserManager.create_parser()
        assert parser is None

    def test_create_parser_for_qwen(self):
        """测试为 Qwen 模型创建 Parser"""
        parser = ParserManager.create_parser(
            tool_parser_name="qwen3_coder",
            reasoning_parser_name="qwen3",
        )
        assert parser is not None
        assert not isinstance(parser, IdentityParser)

        # 测试推理功能
        output = '<thinky>推理内容</thinke>正常回复'
        reasoning, content = parser.extract_reasoning(output)
        assert reasoning == "推理内容"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
