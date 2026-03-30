"""
DeepSeek V3 Tool Parser

支持格式：
<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>get_weather
```json
{"city": "北京"}
```
<｜tool▁call▁end｜><｜tool▁calls▁end｜>
"""

import re
import json
import uuid
from collections.abc import Sequence
from typing import Optional, List, Any

from ..base import (
    BaseToolParser,
    ExtractedToolCallInfo,
    ToolCall,
    DeltaMessage,
    DeltaToolCall,
)


def make_tool_call_id(id_type: str = "random", func_name: str = None, idx: int = None) -> str:
    """生成工具调用 ID"""
    if id_type == "index" and idx is not None:
        return f"call_{idx}"
    return f"call_{uuid.uuid4().hex[:24]}"


class DeepSeekV3ToolParser(BaseToolParser):
    """
    DeepSeek V3 工具解析器

    支持格式：
    <｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>func_name
    ```json
    {"arg": "value"}
    ```
    <｜tool▁call▁end｜><｜tool▁calls▁end｜>
    """

    def __init__(self, tokenizer=None):
        super().__init__(tokenizer)

        # 标记符（Unicode 版本）
        self.tool_calls_start_token: str = "<｜tool▁calls▁begin｜>"
        self.tool_calls_end_token: str = "<｜tool▁calls▁end｜>"
        self.tool_call_start_token: str = "<｜tool▁call▁begin｜>"
        self.tool_call_end_token: str = "<｜tool▁call▁end｜>"
        self.tool_sep: str = "<｜tool▁sep｜>"

        # ASCII 备用版本
        self.alt_tool_calls_start: str = "<|tool_calls_begin|>"
        self.alt_tool_calls_end: str = "<|tool_calls_end|>"
        self.alt_tool_call_start: str = "<|tool_call_begin|>"
        self.alt_tool_call_end: str = "<|tool_call_end|>"
        self.alt_tool_sep: str = "<|tool_sep|>"

        # 正则表达式
        self.tool_call_regex = re.compile(
            r"<｜tool▁call▁begin｜>(?P<type>.*)<｜tool▁sep｜>(?P<function_name>.*)\n```json\n(?P<function_arguments>.*)\n```<｜tool▁call▁end｜>"
        )

        self.stream_tool_call_portion_regex = re.compile(
            r"(?P<type>.*)<｜tool▁sep｜>(?P<function_name>.*)\n```json\n(?P<function_arguments>.*[^\n`])"
        )

        self.stream_tool_call_name_regex = re.compile(
            r"(?P<type>.*)<｜tool▁sep｜>(?P<function_name>.*)\n"
        )

        # 获取 token IDs
        if self.tokenizer:
            self.tool_calls_start_token_id = self.vocab.get(self.tool_calls_start_token)
            self.tool_calls_end_token_id = self.vocab.get(self.tool_calls_end_token)
            self.tool_call_start_token_id = self.vocab.get(self.tool_call_start_token)
            self.tool_call_end_token_id = self.vocab.get(self.tool_call_end_token)

    def _normalize_tokens(self, text: str) -> str:
        """标准化标记符为 Unicode 版本"""
        text = text.replace(self.alt_tool_calls_start, self.tool_calls_start_token)
        text = text.replace(self.alt_tool_calls_end, self.tool_calls_end_token)
        text = text.replace(self.alt_tool_call_start, self.tool_call_start_token)
        text = text.replace(self.alt_tool_call_end, self.tool_call_end_token)
        text = text.replace(self.alt_tool_sep, self.tool_sep)
        return text

    def _detect_format(self, model_output: str) -> tuple:
        """检测使用的标记符格式"""
        if self.tool_calls_start_token in model_output or \
           self.tool_call_start_token in model_output:
            return (
                self.tool_calls_start_token,
                self.tool_calls_end_token,
                self.tool_call_start_token,
                self.tool_call_end_token,
                self.tool_sep,
            )
        else:
            return (
                self.alt_tool_calls_start,
                self.alt_tool_calls_end,
                self.alt_tool_call_start,
                self.alt_tool_call_end,
                self.alt_tool_sep,
            )

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """非流式解析工具调用"""

        # 标准化标记符
        normalized = self._normalize_tokens(model_output)

        if self.tool_calls_start_token not in normalized:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

        try:
            tool_calls = []

            # 提取工具调用区域
            start_idx = normalized.find(self.tool_calls_start_token)
            end_idx = normalized.find(self.tool_calls_end_token)

            if start_idx == -1:
                return ExtractedToolCallInfo(
                    tools_called=False,
                    tool_calls=[],
                    content=model_output
                )

            if end_idx > start_idx:
                tool_calls_region = normalized[start_idx:end_idx + len(self.tool_calls_end_token)]
            else:
                tool_calls_region = normalized[start_idx:]

            # 使用正则匹配
            for match in self.tool_call_regex.finditer(tool_calls_region):
                tool_type = match.group("type")
                function_name = match.group("function_name")
                function_args = match.group("function_arguments")

                tool_calls.append(ToolCall(
                    id=make_tool_call_id(),
                    type=tool_type,
                    name=function_name,
                    arguments=function_args
                ))

            # 提取工具调用前的内容
            content = model_output[:start_idx] if start_idx > 0 else None

            return ExtractedToolCallInfo(
                tools_called=len(tool_calls) > 0,
                tool_calls=tool_calls,
                content=content
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

        # 首次调用时重置状态
        if not previous_text:
            self.reset_streaming_state()

        # 标准化文本
        normalized_current = self._normalize_tokens(current_text)
        normalized_delta = self._normalize_tokens(delta_text)

        # 检查是否有工具调用标记
        tool_calls_start_id = getattr(self, 'tool_calls_start_token_id', None)

        if tool_calls_start_id is not None:
            if tool_calls_start_id not in current_token_ids:
                return DeltaMessage(content=delta_text)

        # 清理标记符
        delta_text_clean = normalized_delta.replace(self.tool_calls_start_token, "")
        delta_text_clean = delta_text_clean.replace(self.tool_calls_end_token, "")

        try:
            # 计算 tool call 开始和结束标记数量
            tool_call_start_id = getattr(self, 'tool_call_start_token_id', None)
            tool_call_end_id = getattr(self, 'tool_call_end_token_id', None)

            if tool_call_start_id is None or tool_call_end_id is None:
                return DeltaMessage(content=delta_text_clean)

            prev_tool_start_count = previous_token_ids.count(tool_call_start_id)
            prev_tool_end_count = previous_token_ids.count(tool_call_end_id)
            cur_tool_start_count = current_token_ids.count(tool_call_start_id)
            cur_tool_end_count = current_token_ids.count(tool_call_end_id)

            tool_call_portion = None
            text_portion = None

            # 情况 1：正在生成文本或结束工具调用
            if (
                cur_tool_start_count == cur_tool_end_count
                and prev_tool_end_count == cur_tool_end_count
                and self.tool_call_end_token not in delta_text_clean
            ):
                return DeltaMessage(content=delta_text_clean)

            # 情况 2：工具调用结束标记在本次增量中
            if self.tool_call_end_token in delta_text_clean:
                full_text = current_text + delta_text_clean
                tool_call_portion = (
                    full_text.split(self.tool_call_start_token)[-1]
                    .split(self.tool_call_end_token)[0]
                    .rstrip()
                )
                delta_text_clean = delta_text_clean.split(self.tool_call_end_token)[0].rstrip()
                text_portion = delta_text_clean.split(self.tool_call_end_token)[-1].lstrip()

            # 情况 3：开始新的工具调用
            if (
                cur_tool_start_count > cur_tool_end_count
                and cur_tool_start_count > prev_tool_start_count
            ):
                if len(delta_token_ids) > 1:
                    tool_call_portion = current_text.split(self.tool_call_start_token)[-1]
                else:
                    return None

                text_portion = None
                self.current_tool_id += 1
                self.current_tool_name_sent = False
                self.streamed_args_for_tool.append("")

            # 情况 4：更新现有工具调用
            elif (
                cur_tool_start_count > cur_tool_end_count
                and cur_tool_start_count == prev_tool_start_count
            ):
                tool_call_portion = current_text.split(self.tool_call_start_token)[-1]
                text_portion = None

            # 情况 5：工具调用正在关闭
            elif (
                cur_tool_start_count == cur_tool_end_count
                and cur_tool_end_count >= prev_tool_end_count
            ):
                if self.prev_tool_call_arr:
                    diff = self.prev_tool_call_arr[self.current_tool_id].get("arguments")
                    if diff and '"}' in delta_text_clean:
                        end_loc = delta_text_clean.rindex('"}')
                        diff = delta_text_clean[:end_loc] + '"}'
                        self.streamed_args_for_tool[self.current_tool_id] += diff
                        return DeltaMessage(
                            tool_calls=[
                                DeltaToolCall(
                                    index=self.current_tool_id,
                                    arguments=diff
                                )
                            ]
                        )
                return None

            else:
                # 其他情况：生成文本
                text = delta_text_clean.replace(self.tool_call_start_token, "")
                text = text.replace(self.tool_call_end_token, "")
                return DeltaMessage(content=text)

            # 解析工具调用内容
            current_tool_call = {}
            if tool_call_portion:
                match = self.stream_tool_call_portion_regex.match(tool_call_portion)
                if match:
                    tool_type, tool_name, tool_args = match.groups()
                    current_tool_call["name"] = tool_name
                    current_tool_call["arguments"] = tool_args
                else:
                    name_match = self.stream_tool_call_name_regex.match(tool_call_portion)
                    if name_match:
                        tool_type, tool_name = name_match.groups()
                        current_tool_call["name"] = tool_name
                        current_tool_call["arguments"] = ""
                    else:
                        return None

            # 发送工具名称
            if not self.current_tool_name_sent:
                function_name = current_tool_call.get("name")
                if function_name:
                    self.current_tool_name_sent = True
                    return DeltaMessage(
                        tool_calls=[
                            DeltaToolCall(
                                index=self.current_tool_id,
                                id=make_tool_call_id(),
                                type="function",
                                name=function_name,
                                arguments=""
                            )
                        ]
                    )
                return None

            # 发送参数增量
            if tool_call_portion is None:
                return DeltaMessage(content=delta_text_clean) if text_portion else None

            # 更新 prev_tool_call_arr
            if len(self.prev_tool_call_arr) <= self.current_tool_id:
                self.prev_tool_call_arr.append({})

            prev_arguments = self.prev_tool_call_arr[self.current_tool_id].get("arguments")
            cur_arguments = current_tool_call.get("arguments")

            if not cur_arguments and not prev_arguments:
                return None
            elif cur_arguments and not prev_arguments:
                self.streamed_args_for_tool[self.current_tool_id] = cur_arguments
                self.prev_tool_call_arr[self.current_tool_id] = current_tool_call
                return DeltaMessage(
                    tool_calls=[
                        DeltaToolCall(
                            index=self.current_tool_id,
                            arguments=cur_arguments
                        )
                    ]
                )
            elif cur_arguments and prev_arguments:
                if (
                    cur_arguments != prev_arguments
                    and len(cur_arguments) > len(prev_arguments)
                    and cur_arguments.startswith(prev_arguments)
                ):
                    delta_arguments = cur_arguments[len(prev_arguments):]
                    self.streamed_args_for_tool[self.current_tool_id] = cur_arguments
                    self.prev_tool_call_arr[self.current_tool_id] = current_tool_call
                    return DeltaMessage(
                        tool_calls=[
                            DeltaToolCall(
                                index=self.current_tool_id,
                                arguments=delta_arguments
                            )
                        ]
                    )

            return None

        except Exception:
            return None
