"""
DeepSeek V3.1 Tool Parser

支持格式（简化版，无 function 类型前缀）：
<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>func_name<｜tool▁sep｜>{"arg": "value"}<｜tool▁call▁end｜><｜tool▁calls▁end｜>
"""

import re
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
    return f"call_{uuid.uuid4().hex[:24]}"


class DeepSeekV31ToolParser(BaseToolParser):
    """
    DeepSeek V3.1 工具解析器

    与 V3 相比，V3.1 格式更简单：
    - 无 function 类型前缀
    - 参数直接是 JSON，不需要 ```json 包裹
    """

    def __init__(self, tokenizer=None):
        super().__init__(tokenizer)

        # 标记符
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
            r"<｜tool▁call▁begin｜>(?P<function_name>.*?)<｜tool▁sep｜>(?P<function_arguments>.*?)<｜tool▁call▁end｜>"
        )

        self.stream_tool_call_portion_regex = re.compile(
            r"(?P<function_name>.*)<｜tool▁sep｜>(?P<function_arguments>.*)"
        )

        self.stream_tool_call_name_regex = re.compile(
            r"(?P<function_name>.*)<｜tool▁sep｜>"
        )

        # 获取 token IDs
        if self.tokenizer:
            self.tool_calls_start_token_id = self.vocab.get(self.tool_calls_start_token)
            self.tool_calls_end_token_id = self.vocab.get(self.tool_calls_end_token)
            self.tool_call_start_token_id = self.vocab.get(self.tool_call_start_token)
            self.tool_call_end_token_id = self.vocab.get(self.tool_call_end_token)

    def _normalize_tokens(self, text: str) -> str:
        """标准化标记符"""
        text = text.replace(self.alt_tool_calls_start, self.tool_calls_start_token)
        text = text.replace(self.alt_tool_calls_end, self.tool_calls_end_token)
        text = text.replace(self.alt_tool_call_start, self.tool_call_start_token)
        text = text.replace(self.alt_tool_call_end, self.tool_call_end_token)
        text = text.replace(self.alt_tool_sep, self.tool_sep)
        return text

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """非流式解析工具调用"""

        normalized = self._normalize_tokens(model_output)

        if self.tool_calls_start_token not in normalized:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

        try:
            tool_calls = []

            # 使用正则匹配
            for match in self.tool_call_regex.finditer(normalized):
                function_name = match.group("function_name")
                function_args = match.group("function_arguments")

                tool_calls.append(ToolCall(
                    id=make_tool_call_id(),
                    type="function",
                    name=function_name,
                    arguments=function_args
                ))

            # 提取工具调用前的内容
            start_idx = normalized.find(self.tool_calls_start_token)
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

        if not previous_text:
            self.reset_streaming_state()

        normalized_current = self._normalize_tokens(current_text)
        normalized_delta = self._normalize_tokens(delta_text)

        tool_calls_start_id = getattr(self, 'tool_calls_start_token_id', None)

        if tool_calls_start_id is not None:
            if tool_calls_start_id not in current_token_ids:
                return DeltaMessage(content=delta_text)

        delta_text_clean = normalized_delta.replace(self.tool_calls_start_token, "")
        delta_text_clean = delta_text_clean.replace(self.tool_calls_end_token, "")

        try:
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

            # 情况 1：生成文本
            if (
                cur_tool_start_count == cur_tool_end_count
                and prev_tool_end_count == cur_tool_end_count
                and self.tool_call_end_token not in delta_text_clean
            ):
                return DeltaMessage(content=delta_text_clean)

            # 情况 2：工具调用结束
            if self.tool_call_end_token in delta_text_clean:
                full_text = current_text + delta_text_clean
                tool_call_portion = (
                    full_text.split(self.tool_call_start_token)[-1]
                    .split(self.tool_call_end_token)[0]
                    .rstrip()
                )
                delta_text_clean = delta_text_clean.split(self.tool_call_end_token)[0].rstrip()
                text_portion = delta_text_clean.split(self.tool_call_end_token)[-1].lstrip()

            # 情况 3：开始新工具调用
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

            # 情况 4：更新工具调用
            elif (
                cur_tool_start_count > cur_tool_end_count
                and cur_tool_start_count == prev_tool_start_count
            ):
                tool_call_portion = current_text.split(self.tool_call_start_token)[-1]
                text_portion = None

            # 情况 5：关闭工具调用
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
                text = delta_text_clean.replace(self.tool_call_start_token, "")
                text = text.replace(self.tool_call_end_token, "")
                return DeltaMessage(content=text)

            # 解析工具调用
            current_tool_call = {}
            if tool_call_portion:
                match = self.stream_tool_call_portion_regex.match(tool_call_portion)
                if match:
                    tool_name, tool_args = match.groups()
                    current_tool_call["name"] = tool_name
                    current_tool_call["arguments"] = tool_args
                else:
                    name_match = self.stream_tool_call_name_regex.match(tool_call_portion)
                    if name_match:
                        tool_name = name_match.group(1)
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

            # 发送参数
            if tool_call_portion is None:
                return DeltaMessage(content=delta_text_clean) if text_portion else None

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
