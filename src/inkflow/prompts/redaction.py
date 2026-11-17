"""脱敏审查提示词。

这个模块只负责把“需要检查的文本”组装成 LLM messages。
真正的模型调用和 JSON 解析放在 services/redaction.py 中，避免 prompt 和业务执行混在一起。
"""

from inkflow.llm import LLMMessage


def build_redaction_messages(numbered_text: str) -> list[LLMMessage]:
    """为“生成脱敏方案”步骤组装聊天消息。"""

    from inkflow.workflow_assets.prompts import builtin_messages
    return builtin_messages('risk-check', {'numbered_text': numbered_text})
