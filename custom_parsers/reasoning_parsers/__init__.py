# 自定义 Reasoning Parsers 目录
#
# 在此目录下放置自定义 parser 文件，文件名即为 parser 名称。
#
# 示例：
#   my_reasoning.py -> parser 名称: "my_reasoning"
#
# parser 类必须继承自 ReasoningParser：
#   from vllm.reasoning.abs_reasoning_parsers import ReasoningParser
#
#   class MyReasoningParser(ReasoningParser):
#       def extract_reasoning(self, model_output, request):
#           ...
#       def extract_reasoning_streaming(self, ...):
#           ...
