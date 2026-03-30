"""
DeepSeek V3.2 Tool Parser

支持 DSML 格式：
<｜DSML｜function_calls>
<｜DSML｜invoke name="get_weather">
<｜DSML｜parameter name="location" string="true">杭州</｜DSML｜parameter>
<｜DSML｜parameter name="date" string="true">2024-01-16</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜function_calls>
"""

import json
import uuid
import re
from collections.abc import Sequence
from typing import Optional, List, Any

from ..base import (
    BaseToolParser,
    ExtractedToolCallInfo,
    ToolCall,
    DeltaMessage,
    DeltaToolCall,
)


def make_tool_call_id() -> str:
    """生成工具调用 ID"""
    return f"call_{uuid.uuid4().hex[:24]}"


class DeepSeekV32ToolParser(BaseToolParser):
    """
    DeepSeek V3.2 工具解析器

    使用 DSML (DeepSeek Markup Language) 格式。
    """

    def __init__(self, tokenizer=None):
        super().__init__(tokenizer)

        # DSML 标记符
        self.dsml_token: str = "｜DSML｜"
        self.dsml_start_check: str = "<" + self.dsml_token
        self.tool_call_start_token: str = "<｜DSML｜function_calls>"
        self.tool_call_end_token: str = "</｜DSML｜function_calls>"
        self.invoke_start_prefix: str = "<｜DSML｜invoke name="
        self.invoke_end_token: str = "</｜DSML｜invoke>"
        self.parameter_prefix: str = "<｜DSML｜parameter name="
        self.parameter_end_token: str = "</｜DSML｜parameter>"

        # 流式状态
        self.is_tool_call_started: bool = False
        self.header_sent: bool = False
        self.current_function_name: Optional[str] = None
        self.current_param_name: Optional[str] = None
        self.current_param_value: str = ""
        self.param_count: int = 0
        self.in_param: bool = False
        self.in_function: bool = False
        self.json_started: bool = False
        self.json_closed: bool = False
        self.accumulated_params: dict = {}

        # 正则表达式
        self.tool_call_complete_regex = re.compile(
            r"<｜DSML｜function_calls>(.*?)</｜DSML｜function_calls>", re.DOTALL
        )
        self.invoke_complete_regex = re.compile(
            r'<｜DSML｜invoke\s+name="([^"]+)"\s*>(.*?)</｜DSML｜invoke>', re.DOTALL
        )
        self.parameter_complete_regex = re.compile(
            r'<｜DSML｜parameter\s+name="([^"]+)"\s+string="(?:true|false)"\s*>(.*?)</｜DSML｜parameter>',
            re.DOTALL,
        )

    def reset_streaming_state(self):
        """重置流式状态"""
        super().reset_streaming_state()
        self.is_tool_call_started = False
        self.header_sent = False
        self.current_function_name = None
        self.current_param_name = None
        self.current_param_value = ""
        self.param_count = 0
        self.in_param = False
        self.in_function = False
        self.json_started = False
        self.json_closed = False
        self.accumulated_params = {}

    def _parse_invoke_params(self, invoke_str: str) -> dict:
        """解析 invoke 中的参数"""
        param_dict = {}
        for param_name, param_val in self.parameter_complete_regex.findall(invoke_str):
            param_dict[param_name] = param_val
        return param_dict

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """非流式解析工具调用"""

        if self.tool_call_start_token not in model_output:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

        try:
            tool_calls = []

            # 查找完整的 tool_call 块
            for tool_call_match in self.tool_call_complete_regex.findall(model_output):
                # 查找所有 invoke
                for invoke_name, invoke_content in self.invoke_complete_regex.findall(tool_call_match):
                    param_dict = self._parse_invoke_params(invoke_content)
                    tool_calls.append(ToolCall(
                        id=make_tool_call_id(),
                        type="function",
                        name=invoke_name,
                        arguments=json.dumps(param_dict, ensure_ascii=False)
                    ))

            if not tool_calls:
                return ExtractedToolCallInfo(
                    tools_called=False,
                    tool_calls=[],
                    content=model_output
                )

            # 提取内容
            first_tool_idx = model_output.find(self.tool_call_start_token)
            content = model_output[:first_tool_idx] if first_tool_idx > 0 else None

            return ExtractedToolCallInfo(
                tools_called=True,
                tool_calls=tool_calls,
                content=content
            )

        except Exception:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

    def _extract_name(self, name_str: str) -> str:
        """从引号字符串中提取名称"""
        name_str = name_str.strip()
        if (name_str.startswith('"') and name_str.endswith('"')) or \
           (name_str.startswith("'") and name_str.endswith("'")):
            return name_str[1:-1]
        return name_str

    def _extract_param_name(self, input_str: str) -> str:
        """提取参数名"""
        start = input_str.find('"') + 1
        end = input_str.find('"', start)
        return input_str[start:end] if start > 0 and end > start else input_str

    def _convert_param_value(self, value: str, param_type: str) -> Any:
        """转换参数值类型"""
        if value.lower() == "null":
            return None

        param_type = param_type.lower()
        if param_type in ["string", "str", "text"]:
            return value
        elif param_type in ["integer", "int"]:
            try:
                return int(value)
            except (ValueError, TypeError):
                return value
        elif param_type in ["number", "float"]:
            try:
                val = float(value)
                return val if val != int(val) else int(val)
            except (ValueError, TypeError):
                return value
        elif param_type in ["boolean", "bool"]:
            return value.lower() in ["true", "1"]
        elif param_type in ["object", "array"]:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        else:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> Optional[DeltaMessage]:
        """流式解析工具调用"""

        if not previous_text:
            self.reset_streaming_state()

        # 无增量文本
        if not delta_text:
            if delta_token_ids:
                complete_calls = len(self.tool_call_complete_regex.findall(current_text))
                if complete_calls > 0 and len(self.prev_tool_call_arr) > 0:
                    open_calls = current_text.count(self.tool_call_start_token) - \
                                current_text.count(self.tool_call_end_token)
                    if open_calls == 0:
                        return DeltaMessage(content="")
                elif not self.is_tool_call_started and current_text:
                    return DeltaMessage(content="")
            return None

        # 处理工具调用开始前的普通内容
        if not self.is_tool_call_started:
            if self.dsml_token in current_text:
                self.is_tool_call_started = True
                if self.dsml_start_check in delta_text:
                    content_before = delta_text[:delta_text.index(self.dsml_start_check)]
                    if content_before:
                        return DeltaMessage(content=content_before)
                return None
            else:
                if delta_text.endswith("<"):
                    return DeltaMessage(content=delta_text[:-1])
                if previous_text and previous_text.endswith("<"):
                    return DeltaMessage(content="<" + delta_text)
                return DeltaMessage(content=delta_text)

        # 查找 invoke 开始位置
        invoke_start_positions = []
        idx = 0
        while True:
            idx = current_text.find(self.invoke_start_prefix, idx)
            if idx == -1:
                break
            invoke_start_positions.append(idx)
            idx += len(self.invoke_start_prefix)

        invoke_starts_count = len(invoke_start_positions)
        if self.current_tool_id >= invoke_starts_count:
            return None

        invoke_start_idx = invoke_start_positions[self.current_tool_id]
        invoke_end_idx = current_text.find(self.invoke_end_token, invoke_start_idx)
        if invoke_end_idx == -1:
            tool_text = current_text[invoke_start_idx:]
        else:
            tool_text = current_text[invoke_start_idx:invoke_end_idx + len(self.invoke_end_token)]

        # 发送函数头
        if not self.header_sent:
            if self.invoke_start_prefix in tool_text:
                func_start = tool_text.find(self.invoke_start_prefix) + len(self.invoke_start_prefix)
                func_end = tool_text.find(">", func_start)

                if func_end != -1:
                    function_name_raw = tool_text[func_start:func_end]
                    self.current_function_name = self._extract_name(function_name_raw)

                    if len(self.prev_tool_call_arr) <= self.current_tool_id:
                        self.prev_tool_call_arr.append({
                            "name": self.current_function_name,
                            "arguments": "{}",
                        })

                    self.header_sent = True
                    self.in_function = True

                    return DeltaMessage(
                        tool_calls=[
                            DeltaToolCall(
                                index=self.current_tool_id,
                                id=make_tool_call_id(),
                                type="function",
                                name=self.current_function_name,
                                arguments=""
                            )
                        ]
                    )
            return None

        # 处理函数体
        if self.in_function:
            # 发送开始括号
            if not self.json_started:
                self.json_started = True
                return DeltaMessage(
                    tool_calls=[
                        DeltaToolCall(
                            index=self.current_tool_id,
                            arguments="{"
                        )
                    ]
                )

            # 检查函数结束
            if not self.json_closed and self.invoke_end_token in tool_text:
                total_param_count = tool_text.count(self.parameter_prefix)
                if self.param_count >= total_param_count:
                    self.json_closed = True

                    # 解析完整参数
                    invoke_start = tool_text.find(self.invoke_start_prefix) + len(self.invoke_start_prefix)
                    invoke_content_end = tool_text.find(self.invoke_end_token, invoke_start)
                    if invoke_content_end != -1:
                        invoke_content = tool_text[invoke_start:invoke_content_end]
                        try:
                            invoke_params = self._parse_invoke_params(invoke_content)
                            if invoke_params and self.current_tool_id < len(self.prev_tool_call_arr):
                                self.prev_tool_call_arr[self.current_tool_id]["arguments"] = \
                                    json.dumps(invoke_params, ensure_ascii=False)
                        except Exception:
                            pass

                    self.json_closed = True
                    self.in_function = False
                    self.accumulated_params = {}

                    return DeltaMessage(
                        tool_calls=[
                            DeltaToolCall(
                                index=self.current_tool_id,
                                arguments="}"
                            )
                        ]
                    )

            # 查找参数
            param_starts = []
            idx = 0
            while True:
                idx = tool_text.find(self.parameter_prefix, idx)
                if idx == -1:
                    break
                param_starts.append(idx)
                idx += len(self.parameter_prefix)

            if not self.in_param and self.param_count < len(param_starts):
                param_idx = param_starts[self.param_count]
                param_start = param_idx + len(self.parameter_prefix)
                remaining = tool_text[param_start:]

                if ">" in remaining:
                    name_end = remaining.find(">")
                    self.current_param_name = self._extract_param_name(remaining[:name_end])

                    value_start = param_start + name_end + 1
                    value_text = tool_text[value_start:]
                    if value_text.startswith("\n"):
                        value_text = value_text[1:]

                    param_end_idx = value_text.find(self.parameter_end_token)
                    if param_end_idx == -1:
                        next_param_idx = value_text.find(self.parameter_prefix)
                        func_end_idx = value_text.find(self.invoke_end_token)
                        if next_param_idx != -1 and (func_end_idx == -1 or next_param_idx < func_end_idx):
                            param_end_idx = next_param_idx
                        elif func_end_idx != -1:
                            param_end_idx = func_end_idx
                        elif self.invoke_end_token in tool_text:
                            param_end_idx = len(value_text)
                        else:
                            return None

                    if param_end_idx != -1:
                        param_value = value_text[:param_end_idx]
                        if param_value.endswith("\n"):
                            param_value = param_value[:-1]

                        self.accumulated_params[self.current_param_name] = param_value

                        # 类型转换
                        converted_value = param_value  # 默认字符串

                        if self.param_count == 0:
                            json_fragment = f'"{self.current_param_name}": {json.dumps(converted_value, ensure_ascii=False)}'
                        else:
                            json_fragment = f', "{self.current_param_name}": {json.dumps(converted_value, ensure_ascii=False)}'

                        self.param_count += 1

                        return DeltaMessage(
                            tool_calls=[
                                DeltaToolCall(
                                    index=self.current_tool_id,
                                    arguments=json_fragment
                                )
                            ]
                        )

        return None
