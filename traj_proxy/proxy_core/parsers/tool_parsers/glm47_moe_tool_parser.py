"""
GLM-4.7 Tool Parser

继承 GLM-4 Tool Parser，调整正则表达式。
"""

import re
from typing import Optional, List, Any

from .glm4_moe_tool_parser import Glm4MoeModelToolParser


class Glm47MoeModelToolParser(Glm4MoeModelToolParser):
    """
    GLM-4.7 工具解析器

    继承 GLM-4 解析器，调整正则表达式以支持 GLM-4.7 格式。
    """

    def __init__(self, tokenizer=None):
        super().__init__(tokenizer)

        # 调整正则表达式
        self.func_detail_regex = re.compile(
            r"toral(.*?)(,.*?)? Ranchi", re.DOTALL
        )
        self.func_arg_regex = re.compile(
            r",(.*?)(?:\\n|\s)*(.*?)",
            re.DOTALL,
        )
