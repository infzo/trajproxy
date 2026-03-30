"""
GLM-4 Tool Parser

支持格式：
toralfunction_name
,arg_key1,arg_val1, arg_key2,arg_val2,
Ranchi

支持增量字符串流式解析。
"""

import ast
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


class Glm4MoeModelToolParser(BaseToolParser):
    """
    GLM-4 工具解析器

    支持 GLM-4 特有的工具调用格式，并提供增量字符串流式解析。
    """

    def __init__(self, tokenizer=None):
        super().__init__(tokenizer)

        # 标记符
        self.tool_call_start_token: str = "toral"
        self.tool_call_end_token: str = " Ranchi"
        self.arg_key_start: str = ","
        self.arg_key_end: str = ","
        self.arg_val_start: str = ""
        self.arg_val_end: str = ""

        # 工具调用开始标记
        self.tool_calls_start_token = self.tool_call_start_token

        # 正则表达式
        self.func_call_regex = re.compile(r"toral.*? Ranchi", re.DOTALL)
        self.func_detail_regex = re.compile(
            r"toral([^\n]*)\n(.*) Ranchi", re.DOTALL
        )
        self.func_arg_regex = re.compile(
            r",(.*?),\s*(.*?)",
            re.DOTALL
        )

        # 流式状态
        self._buffer: str = ""
        self._in_tool_call: bool = False
        self._current_tool_name: Optional[str] = None
        self._pending_key: Optional[str] = None
        self._streaming_string_value: bool = False
        self._tool_call_ids: List[str] = []
        self._args_started: List[bool] = []
        self._args_closed: List[bool] = []
        self._seen_keys: List[set] = []

    @staticmethod
    def _deserialize(value: str) -> Any:
        """反序列化值"""
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass

        return value

    @staticmethod
    def _json_escape_string_content(s: str) -> str:
        """JSON 转义字符串内容"""
        if not s:
            return ""
        return json.dumps(s, ensure_ascii=False)[1:-1]

    @staticmethod
    def _is_string_type(
        tool_name: str,
        arg_name: str,
        tools: Optional[List[dict]]
    ) -> bool:
        """检查参数是否为字符串类型"""
        if tools is None:
            return False
        for tool in tools:
            func = tool.get("function", {})
            if func.get("name") != tool_name:
                continue
            params = func.get("parameters", {})
            arg_type = params.get("properties", {}).get(arg_name, {}).get("type", None)
            return arg_type == "string"
        return False

    @staticmethod
    def _tools_enabled(request: Optional[Any]) -> bool:
        """检查是否启用工具"""
        if request is None:
            return True
        try:
            tools = getattr(request, "tools", None)
            tool_choice = getattr(request, "tool_choice", None)
            return bool(tools) and tool_choice != "none"
        except Exception:
            return True

    def extract_tool_calls(
        self,
        model_output: str,
        tools: Optional[List[dict]] = None,
        request: Optional[Any] = None
    ) -> ExtractedToolCallInfo:
        """非流式解析工具调用"""
        matched_tool_calls = self.func_call_regex.findall(model_output)

        try:
            tool_calls: List[ToolCall] = []
            for match in matched_tool_calls:
                tc_detail = self.func_detail_regex.search(match)
                if not tc_detail:
                    continue
                tc_name = tc_detail.group(1).strip()
                tc_args = tc_detail.group(2)
                pairs = self.func_arg_regex.findall(tc_args) if tc_args else []
                arg_dict: dict = {}
                for key, value in pairs:
                    arg_key = key.strip()
                    arg_val = value.strip()
                    if not self._is_string_type(tc_name, arg_key, tools):
                        arg_val = self._deserialize(arg_val)
                    arg_dict[arg_key] = arg_val

                tool_calls.append(ToolCall(
                    id=make_tool_call_id(),
                    type="function",
                    name=tc_name,
                    arguments=json.dumps(arg_dict, ensure_ascii=False)
                ))

            if len(tool_calls) > 0:
                content = model_output[:model_output.find(self.tool_calls_start_token)]
                return ExtractedToolCallInfo(
                    tools_called=True,
                    tool_calls=tool_calls,
                    content=content if content else None
                )

            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

        except Exception:
            return ExtractedToolCallInfo(
                tools_called=False,
                tool_calls=[],
                content=model_output
            )

    def _ensure_tool_state(self) -> None:
        """确保工具状态数组已初始化"""
        while len(self._tool_call_ids) <= self.current_tool_id:
            self._tool_call_ids.append(make_tool_call_id())
        while len(self.streamed_args_for_tool) <= self.current_tool_id:
            self.streamed_args_for_tool.append("")
        while len(self.prev_tool_call_arr) <= self.current_tool_id:
            self.prev_tool_call_arr.append({})
        while len(self._args_started) <= self.current_tool_id:
            self._args_started.append(False)
        while len(self._args_closed) <= self.current_tool_id:
            self._args_closed.append(False)
        while len(self._seen_keys) <= self.current_tool_id:
            self._seen_keys.append(set())

    def _begin_tool_call(self) -> None:
        """开始工具调用"""
        if self.current_tool_id == -1:
            self.current_tool_id = 0
        else:
            self.current_tool_id += 1
        self._ensure_tool_state()
        self.current_tool_name_sent = False
        self._current_tool_name = None
        self._pending_key = None
        self._streaming_string_value = False
        self._in_tool_call = True

    def _finish_tool_call(self) -> None:
        """结束工具调用"""
        self._in_tool_call = False
        self._current_tool_name = None
        self._pending_key = None
        self._streaming_string_value = False

    def _emit_tool_name_delta(self, tool_name: str) -> DeltaMessage:
        """发送工具名称增量"""
        return DeltaMessage(
            tool_calls=[
                DeltaToolCall(
                    index=self.current_tool_id,
                    id=self._tool_call_ids[self.current_tool_id],
                    type="function",
                    name=tool_name,
                    arguments=""
                )
            ]
        )

    def _emit_tool_args_delta(self, fragment: str) -> DeltaMessage:
        """发送参数增量"""
        return DeltaMessage(
            tool_calls=[
                DeltaToolCall(
                    index=self.current_tool_id,
                    arguments=fragment
                )
            ]
        )

    def _append_arg_fragment(self, key: str, raw_val: str) -> Optional[str]:
        """添加参数片段"""
        key = key.strip()
        if not key:
            return None
        if key in self._seen_keys[self.current_tool_id]:
            return None

        val_obj = self._deserialize(raw_val)
        key_json = json.dumps(key, ensure_ascii=False)
        val_json = json.dumps(val_obj, ensure_ascii=False)

        if not self._args_started[self.current_tool_id]:
            fragment = "{" + key_json + ":" + val_json
            self._args_started[self.current_tool_id] = True
        else:
            fragment = "," + key_json + ":" + val_json

        self._seen_keys[self.current_tool_id].add(key)
        self.streamed_args_for_tool[self.current_tool_id] += fragment
        return fragment

    def _close_args_if_needed(self) -> Optional[str]:
        """关闭参数"""
        if self._args_closed[self.current_tool_id]:
            return None
        self._args_closed[self.current_tool_id] = True
        if not self._args_started[self.current_tool_id]:
            fragment = "{}"
            self.streamed_args_for_tool[self.current_tool_id] = fragment
        else:
            fragment = "}"
            self.streamed_args_for_tool[self.current_tool_id] += fragment
        return fragment

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
            self._buffer = ""
            self._in_tool_call = False
            self._current_tool_name = None
            self._pending_key = None
            self._streaming_string_value = False

        if not delta_text:
            return None

        self._buffer += delta_text

        while True:
            if not self._in_tool_call:
                start_idx = self._buffer.find(self.tool_call_start_token)
                if start_idx == -1:
                    # 检查部分匹配
                    for i in range(1, len(self.tool_call_start_token)):
                        if self._buffer.endswith(self.tool_call_start_token[:i]):
                            out = self._buffer[:-i]
                            self._buffer = self._buffer[-i:]
                            return DeltaMessage(content=out) if out else None
                    out = self._buffer
                    self._buffer = ""
                    return DeltaMessage(content=out) if out else None

                if start_idx > 0:
                    out = self._buffer[:start_idx]
                    self._buffer = self._buffer[start_idx:]
                    return DeltaMessage(content=out) if out else None

                self._buffer = self._buffer[len(self.tool_call_start_token):]
                self._begin_tool_call()
                continue

            # 解析工具名称
            if not self.current_tool_name_sent:
                nl = self._buffer.find("\n")
                ak = self._buffer.find(self.arg_key_start)
                end = self._buffer.find(self.tool_call_end_token)
                candidates = [i for i in [nl, ak, end] if i != -1]
                if not candidates:
                    return None
                cut = min(candidates)
                tool_name = self._buffer[:cut].strip()

                if tool_name == "" and cut == end:
                    self._buffer = self._buffer[end + len(self.tool_call_end_token):]
                    self._finish_tool_call()
                    continue

                if cut == nl:
                    self._buffer = self._buffer[nl + 1:]
                else:
                    self._buffer = self._buffer[cut:]

                self._current_tool_name = tool_name
                self.current_tool_name_sent = True
                return self._emit_tool_name_delta(tool_name)

            # 处理增量字符串值
            if self._streaming_string_value:
                val_end = self._buffer.find(self.arg_val_end)
                if val_end != -1:
                    raw_content = self._buffer[:val_end]
                    self._buffer = self._buffer[val_end + len(self.arg_val_end):]
                    self._streaming_string_value = False
                    self._pending_key = None

                    escaped = self._json_escape_string_content(raw_content)
                    frag = escaped + '"'
                    self.streamed_args_for_tool[self.current_tool_id] += frag
                    return self._emit_tool_args_delta(frag)
                else:
                    # 检查部分匹配
                    safe_len = len(self._buffer)
                    for i in range(1, len(self.arg_val_end)):
                        if self._buffer.endswith(self.arg_val_end[:i]):
                            safe_len = len(self._buffer) - i
                            break

                    if safe_len > 0:
                        to_emit = self._buffer[:safe_len]
                        self._buffer = self._buffer[safe_len:]
                        escaped = self._json_escape_string_content(to_emit)
                        if escaped:
                            self.streamed_args_for_tool[self.current_tool_id] += escaped
                            return self._emit_tool_args_delta(escaped)
                    return None

            # 有待处理的键
            if self._pending_key is not None:
                val_pos = self._buffer.find(self.arg_val_start)
                if val_pos == -1:
                    return None
                if val_pos > 0:
                    self._buffer = self._buffer[val_pos:]

                key = (self._pending_key or "").strip()

                is_string = self._is_string_type(
                    self._current_tool_name or "", key, tools
                )

                if is_string:
                    self._buffer = self._buffer[len(self.arg_val_start):]

                    if key in self._seen_keys[self.current_tool_id]:
                        self._pending_key = None
                        continue

                    self._seen_keys[self.current_tool_id].add(key)
                    key_json = json.dumps(key, ensure_ascii=False)

                    if not self._args_started[self.current_tool_id]:
                        frag = "{" + key_json + ':"'
                        self._args_started[self.current_tool_id] = True
                    else:
                        frag = "," + key_json + ':"'

                    self.streamed_args_for_tool[self.current_tool_id] += frag
                    self._streaming_string_value = True
                    return self._emit_tool_args_delta(frag)
                else:
                    val_end = self._buffer.find(self.arg_val_end)
                    if val_end == -1:
                        return None

                    raw_val = self._buffer[len(self.arg_val_start):val_end].strip()
                    self._buffer = self._buffer[val_end + len(self.arg_val_end):]
                    self._pending_key = None

                    frag = self._append_arg_fragment(key=key, raw_val=raw_val)
                    if frag:
                        return self._emit_tool_args_delta(frag)
                    continue

            # 解析下一个参数或关闭
            end_pos = self._buffer.find(self.tool_call_end_token)
            key_pos = self._buffer.find(self.arg_key_start)

            if end_pos != -1 and (key_pos == -1 or end_pos < key_pos):
                self._buffer = self._buffer[end_pos + len(self.tool_call_end_token):]
                frag = self._close_args_if_needed()
                self._finish_tool_call()
                return self._emit_tool_args_delta(frag) if frag else None

            if key_pos == -1:
                return None
            if key_pos > 0:
                self._buffer = self._buffer[key_pos:]

            key_end = self._buffer.find(self.arg_key_end)
            if key_end == -1:
                return None
            key = self._buffer[len(self.arg_key_start):key_end]
            self._buffer = self._buffer[key_end + len(self.arg_key_end):]
            self._pending_key = key
            continue
