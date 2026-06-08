# 自定义 Tool Parsers 目录
#
# 在此目录下放置自定义 parser 文件，文件名即为 parser 名称。
#
# 示例：
#   my_parser.py -> parser 名称: "my_parser"
#
# parser 类必须继承自 ToolParser：
#   from vllm.tool_parsers.abstract_tool_parser import ToolParser
#
#   class MyParser(ToolParser):
#       def extract_tool_calls(self, model_output, request):
#           ...
#       def extract_tool_calls_streaming(self, ...):
#           ...
