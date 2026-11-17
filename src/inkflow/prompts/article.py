"""文章生成提示词。

这个模块只负责把“脱敏后的正文”和“用户补充要求”组装成 LLM messages。
服务层会负责调用模型、解析 JSON，以及必要时向用户追问。
"""

from inkflow.llm import LLMMessage


def build_article_messages(
    text: str,
    today: str,
    qa_history: list[dict[str, str]],
    extra_instruction: str = "",
) -> list[LLMMessage]:
    """为“生成结构化文章 JSON”步骤组装聊天消息。"""

    history_text = _format_qa_history(qa_history)
    instruction_text = extra_instruction.strip() or "无"

    from inkflow.workflow_assets.prompts import builtin_messages
    return builtin_messages('article-generation', {'text': text, 'today': today,
        'history_text': history_text, 'instruction_text': instruction_text})


def _format_qa_history(qa_history: list[dict[str, str]]) -> str:
    """把模型追问和用户回答整理成 prompt 中容易阅读的文本。"""

    if not qa_history:
        return "无"

    return "\n".join(
        f"- 问：{item.get('question', '')}\n  答：{item.get('answer', '')}"
        for item in qa_history
    )
