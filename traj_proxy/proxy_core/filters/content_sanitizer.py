"""
ContentSanitizer - 消息内容净化器

将 system message 中的动态变化字段归一化为固定值，
使 TITO 模式的前缀匹配缓存保持稳定命中率。

当前规则：
  - Anthropic billing header 中的 cch 字段（5 位小写 hex → 00000）

背景：
  Claude Code 的 system prompt 包含
  `x-anthropic-billing-header: cc_version=...; cc_entrypoint=...; cch=XXXXX;`
  其中 cch 是 Anthropic 内部 prompt cache hash，每次新会话变化。
  该字段不承载业务语义（对推理模型输出无影响），但在 TITO 模式下
  会破坏 Jinja 渲染后的 prompt token 序列一致性，导致前缀缓存失效。

依赖关系：
  - 被 MessageConverter 通过构造函数注入
  - 不依赖任何外部服务或数据库
  - 使用 traj_proxy.utils.logger 输出调试信息（遵守项目脱敏规则）
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from traj_proxy.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SanitizeRule:
    """单条净化规则（不可变，线程安全）

    Attributes:
        name: 规则名，用于日志和指标标识
        pattern: 已编译的正则表达式
        replacement: 替换字符串，支持 group backreference
    """
    name: str
    pattern: re.Pattern
    replacement: str


# 默认规则：归一化 Anthropic billing header 的 cch 字段
# 精确 5 位小写 hex + \b 边界约束：
#   - 匹配示例：cch=92bce、cch=5a3b9、cch=12345;
#   - 不匹配示例：
#       cch=XYZ（大写）、cch=abc（不足 5 位）、
#       cch=abcdef（6 位，\b 在 e/f 之间不存在）
# \b 边界防止"部分匹配"：如 cch=abcdef 不会被误替换为 cch=00000f
# 格式漂移（如 Anthropic 改用 base64 / 不同长度）时正则不匹配，
# 触发可观测告警（DEBUG 日志无命中 + 缓存命中率下降），而非静默放过。
_DEFAULT_RULES: List[SanitizeRule] = [
    SanitizeRule(
        name="cch_normalization",
        pattern=re.compile(r"(cch=)[0-9a-f]{5}\b"),
        replacement=r"\g<1>00000",
    ),
]


class ContentSanitizer:
    """消息内容净化器

    仅在 TITO 模式下使用（由 Processor._create_token_pipeline 注入）。
    识别并归一化 system message 中的动态变化字段，保障前缀缓存命中率。

    设计约束：
      1. 仅处理 role == "system" 的消息，避免篡改 user/assistant 输入语义
      2. 仅修改 content 字段（str 类型），不触碰 tool_calls.arguments 等结构
      3. 调用方必须在 deep copy 后的消息上调用 apply()，方法本身不拷贝数据
      4. 不记录具体被替换的内容值，遵守项目日志脱敏规则

    已知局限：
      - 若 Anthropic 变更 cch 格式（如改 base64、变长度），正则会不匹配。
        此时缓存命中率下降 + DEBUG 日志零命中 → 运维介入升级规则。
      - 模型侧将看到 cch=00000 而非真实值。由于 cch 是 Anthropic 内部
        缓存 hash（不承载语义），且 TITO 模式下发送到非 Anthropic 模型，
        对输出无影响。
    """

    def __init__(self, rules: Optional[List[SanitizeRule]] = None) -> None:
        """初始化净化器

        Args:
            rules: 净化规则列表。默认使用 _DEFAULT_RULES。
                   传入空列表可禁用净化（用于测试场景）。
        """
        self._rules: List[SanitizeRule] = (
            rules if rules is not None else _DEFAULT_RULES
        )

    def apply(self, message: Dict) -> None:
        """对单条消息执行 in-place 净化

        调用方必须保证传入的是 deep copy 后的消息（本方法不拷贝）。

        Args:
            message: 单条 OpenAI Message dict（必须已 deep copy）

        行为：
            - 仅当 message["role"] == "system" 时执行替换
            - 仅修改 message["content"]（当其类型为 str 时）
            - 其他字段（role、name、tool_calls 等）完全不受影响
            - 命中时输出 DEBUG 日志记录规则名和匹配次数（不记录具体值）
        """
        if message.get("role") != "system":
            return

        content = message.get("content")
        if not isinstance(content, str):
            return

        total_matches = 0
        for rule in self._rules:
            new_content, n = rule.pattern.subn(rule.replacement, content)
            if n > 0:
                content = new_content
                total_matches += n

        if total_matches > 0:
            message["content"] = content
            logger.debug(
                f"[内容净化] 规则命中: "
                f"matches={total_matches}, role=system"
            )
