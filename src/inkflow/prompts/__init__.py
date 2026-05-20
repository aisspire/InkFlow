"""InkFlow 的提示词入口。

提示词模块负责把业务输入组装成模型 messages，
不直接读取状态，也不直接调用 LLM。
"""

from inkflow.prompts.draft import build_draft_messages

__all__ = ["build_draft_messages"]
