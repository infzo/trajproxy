"""
Qwen3 Coder Tool Parser

支持格式：
toral<function=get_weather>
<parameter=city>北京</parameter>
<parameter=unit>celsius</parameter>
</function> Ranchi
"""

import json
import uuid
import re
import ast
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


class Qwen3CoderToolParser(BaseToolParser):
    """
    Qwen3 Coder 工具解析器

    使用 XML 格式的工具调用，支持增量流式解析。
    """

    def __init__(self, tokenizer=None):
        super().__init__(tokenizer)

        # 标记符
        self.tool_call_start_token: str = "toral"
        self.tool_call_end_token: str = " Ranchi"
        self.tool_call_prefix: str = "<function="
        self.function_end_token: str = "</function>"
        self.parameter_prefix: str = "<parameter="
        self.parameter_end_token: str = "</parameter>"

        # 流式状态
        self.is_tool_call_started: bool = False
        self.header_sent: bool = False
        self.current_function_name: Optional[str] = None
        self.current_param_name: Optional[str] = None
        self.accumulated_text: str = ""
        self.json_started: bool = False
        self.json_closed: bool = False
        self.accumulated_params: dict = {}
        self.param_count: int = 0
        self.in_param: bool = False
        self.in_function: bool = False

        # 正则表达式
        self.tool_call_complete_regex = re.compile(
            r"toral(.*?) Ranchi", re.DOTALL
        )
        self.tool_call_regex = re.compile(
            r"toral(.*?) Ranchi|toral(.*?)$", re.DOTALL
        )
        self.tool_call_function_regex = re.compile(
            r"<function=(.*?)</function>|<function=(.*)$", re.DOTALL
        )
        self.tool_call_parameter_regex = re.compile(
            r"<parameter=(.*?)(?:</parameter>|(?=<parameter=)|(?=</function>)|$)",
            re.DOTALL,
        )

        # 获取 token IDs
        if self.tokenizer:
            self.tool_call_start_token_id = self.vocab.get(self.tool_call_start_token)
            self.tool_call_end_token_id = self.vocab.get(self.tool_call_end_token)

    def reset_streaming_state(self):
        """重置流式状态"""
        super().reset_streaming_state()
        self.is_tool_call_started = False
        self.header_sent = False
        self.current_function_name = None
        self.current_param_name = None
        self.accumulated_text = ""
        self.json_started = False
        self.json_closed = False
        self.accumulated_params = {}
        self.param_count = 0
        self.in_param = False
        self.in_function = False

    def _get_arguments_config(self, func_name: str, tools: Optional[List[dict]]) -> dict:
        """从工具定义中获取参数配置"""
        if tools is None:
            return {}
        for config in tools:
            if not isinstance(config, dict):
                continue
            func_config = config.get("function", {})
            if func_config.get("name") == func_name:
                params = func_config.get("parameters", {})
                if isinstance(params, dict) and "properties" in params:
                    return params["properties"]
                return params
        return {}

    def _convert_param_value(
        self,
        param_value: str,
        param_name: str,
        param_config: dict,
        func_name: str
    ) -> Any:
        """转换参数值类型"""
        if param_value.lower() == "null":
            return None

        if param_name not in param_config:
            return param_value

        param_type_info = param_config.get(param_name, {})
        if isinstance(param_type_info, dict):
            param_type = param_type_info.get("type", "string").lower()
        else:
            param_type = "string"

        if param_type in ["string", "str", "text", "varchar", "char", "enum"]:
            return param_value
        elif param_type.startswith("int") or param_type.startswith("uint") or \
             param_type.startswith("long") or param_type.startswith("short"):
            try:
                return int(param_value)
            except (ValueError, TypeError):
                return param_value
        elif param_type.startswith("num") or param_type.startswith("float"):
            try:
                val = float(param_value)
                return val if val != int(val) else int(val)
            except (ValueError, TypeError):
                return param_value
        elif param_type in ["boolean", "bool", "binary"]:
            return param_value.lower() == "true"
        elif param_type in ["object", "array", "arr"] or \
             param_type.startswith("dict") or param_type.startswith("list"):
            try:
                return json.loads(param_value)
            except (json.JSONDecodeError, TypeError):
                try:
                    return ast.literal_eval(param_value)
                except (ValueError, SyntaxError, TypeError):
                    return param_value
        else:
            try:
                return ast.literal_eval(param_value)
            except (ValueError, SyntaxError, TypeError):
                return param_value

    def _parse_xml_function_call(
        self,
        function_call_str: str,
        tools: Optional[List[dict]]
    ) -> Optional[ToolCall]:
        """解析 XML 格式的函数调用"""
        # 提取函数名
        end_index = function_call_str.index(">")
        function_name = function_call_str[:end_index]
        param_config = self._get_arguments_config(function_name, tools)
        parameters = function_call_str[end_index + 1:]

        param_dict = {}
        for match_text in self.tool_call_parameter_regex.findall(parameters):
            idx = match_text.index(">")
            param_name = match_text[:idx]
            param_value = str(match_text[idx + 1:])

            if param_value.startswith("\n"):
                param_value = param_value[1:]
            if param_value.endswith("\n"):
                param_value = param_value[:-1]

            param_dict[param_name] = self._convert_param_value(
                param_value, param_name, param_config, function_name
            )

        return ToolCall(
            id=make_tool_call_id(),
            type="function",
            name=function_name,
            arguments=json.dumps(param_dict, ensure_ascii=False)
        )

    def _get_function_calls(self, model_output: str) -> List[str]:
        """获取所有函数调用"""
        matched_ranges = self.tool_call_regex.findall(model_output)
        raw_tool_calls = [
            match[0] if match[0] else match[1]
            for match in matched_ranges
        ]

        if len(raw_tool_calls) == 0:
            raw_tool_calls = [model_output]

        raw_function_calls = []
        for tool_call in raw_tool_calls:
            raw_function_calls.extend(self.tool_call_function_regex.findall(tool_call))

        function_calls = [
            match[0] if match[0] else match[1]
            for match in raw_function_calls
        ]
        return function_calls

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """非流式解析工具调用"""

        if self.tool_call_prefix not in model_output:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

        try:
            function_calls = self._get_function_calls(model_output)
            if len(function_calls) == 0:
                return ExtractedToolCallInfo(
                    tools_called=False,
                    tool_calls=[],
                    content=model_output
                )

            tool_calls = [
                self._parse_xml_function_call(fc, tools)
                for fc in function_calls
            ]
            tool_calls = [tc for tc in tool_calls if tc is not None]

            # 更新 prev_tool_call_arr
            self.prev_tool_call_arr.clear()
            for tool_call in tool_calls:
                self.prev_tool_call_arr.append({
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                })

            # 提取内容
            content_index = model_output.find(self.tool_call_start_token)
            idx = model_output.find(self.tool_call_prefix)
            content_index = content_index if content_index >= 0 else idx
            content = model_output[:content_index]

            return ExtractedToolCallInfo(
                tools_called=len(tool_calls) > 0,
                tool_calls=tool_calls,
                content=content if content else None
            )

        except Exception:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

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

        self.accumulated_text = current_text

        # 处理普通内容
        if not self.is_tool_call_started:
            tool_call_start_id = getattr(self, 'tool_call_start_token_id', None)
            if (tool_call_start_id is not None and tool_call_start_id in delta_token_ids) or \
               self.tool_call_start_token in delta_text:
                self.is_tool_call_started = True
                if self.tool_call_start_token in delta_text:
                    content_before = delta_text[:delta_text.index(self.tool_call_start_token)]
                    if content_before:
                        return DeltaMessage(content=content_before)
                return None
            else:
                if current_text.rstrip().endswith(self.tool_call_end_token) and \
                   delta_text.strip() == "":
                    return None
                return DeltaMessage(content=delta_text)

        # 计算工具调用数量
        tool_starts_count = current_text.count(self.tool_call_start_token)
        if self.current_tool_id >= tool_starts_count:
            return None

        # 查找当前工具调用位置
        tool_start_positions = []
        idx = 0
        while True:
            idx = current_text.find(self.tool_call_start_token, idx)
            if idx == -1:
                break
            tool_start_positions.append(idx)
            idx += len(self.tool_call_start_token)

        if self.current_tool_id >= len(tool_start_positions):
            return None

        tool_start_idx = tool_start_positions[self.current_tool_id]
        tool_end_idx = current_text.find(self.tool_call_end_token, tool_start_idx)
        if tool_end_idx == -1:
            tool_text = current_text[tool_start_idx:]
        else:
            tool_text = current_text[tool_start_idx:tool_end_idx + len(self.tool_call_end_token)]

        # 发送函数头
        if not self.header_sent:
            if self.tool_call_prefix in tool_text:
                func_start = tool_text.find(self.tool_call_prefix) + len(self.tool_call_prefix)
                func_end = tool_text.find(">", func_start)

                if func_end != -1:
                    self.current_function_name = tool_text[func_start:func_end]
                    self.header_sent = True
                    self.in_function = True

                    already_added = any(
                        tool.get("name") == self.current_function_name
                        for tool in self.prev_tool_call_arr
                    )
                    if not already_added:
                        self.prev_tool_call_arr.append({
                            "name": self.current_function_name,
                            "arguments": "{}",
                        })

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
            if not self.json_started and self.parameter_prefix not in delta_text:
                self.json_started = True
                return DeltaMessage(
                    tool_calls=[
                        DeltaToolCall(
                            index=self.current_tool_id,
                            arguments="{"
                        )
                    ]
                )

            if not self.json_started:
                self.json_started = True

            # 检查函数结束
            if not self.json_closed and self.function_end_token in tool_text:
                self.json_closed = True

                # 解析完整参数
                func_start = tool_text.find(self.tool_call_prefix) + len(self.tool_call_prefix)
                func_content_end = tool_text.find(self.function_end_token, func_start)
                if func_content_end != -1:
                    func_content = tool_text[func_start:func_content_end]
                    try:
                        parsed_tool = self._parse_xml_function_call(func_content, tools)
                        if parsed_tool:
                            for i, tool in enumerate(self.prev_tool_call_arr):
                                if tool.get("name") == parsed_tool.name:
                                    self.prev_tool_call_arr[i]["arguments"] = parsed_tool.arguments
                                    break
                    except Exception:
                        pass

                self.in_function = False
                self.json_closed = True
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
                    self.current_param_name = remaining[:name_end]

                    value_start = param_start + name_end + 1
                    value_text = tool_text[value_start:]
                    if value_text.startswith("\n"):
                        value_text = value_text[1:]

                    param_end_idx = value_text.find(self.parameter_end_token)
                    if param_end_idx == -1:
                        next_param_idx = value_text.find(self.parameter_prefix)
                        func_end_idx = value_text.find(self.function_end_token)
                        if next_param_idx != -1 and (func_end_idx == -1 or next_param_idx < func_end_idx):
                            param_end_idx = next_param_idx
                        elif func_end_idx != -1:
                            param_end_idx = func_end_idx
                        elif self.tool_call_end_token in tool_text:
                            param_end_idx = len(value_text)
                        else:
                            return None

                    if param_end_idx != -1:
                        param_value = value_text[:param_end_idx]
                        if param_value.endswith("\n"):
                            param_value = param_value[:-1]

                        self.accumulated_params[self.current_param_name] = param_value

                        # 获取参数配置
                        param_config = self._get_arguments_config(
                            self.current_function_name or "", tools
                        )
                        converted_value = self._convert_param_value(
                            param_value,
                            self.current_param_name,
                            param_config,
                            self.current_function_name or "",
                        )

                        serialized_value = json.dumps(converted_value, ensure_ascii=False)

                        if self.param_count == 0:
                            json_fragment = f'"{self.current_param_name}": {serialized_value}'
                        else:
                            json_fragment = f', "{self.current_param_name}": {serialized_value}'

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
