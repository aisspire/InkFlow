"""InkFlow 的提示词组装模块。

这里不直接调用 LLM，只负责把业务输入转换成模型能理解的 messages。
未来如果要增加标题生成、润色、事实检查等提示词，可以继续在这里添加
类似 build_xxx_messages() 的函数。
"""

from inkflow.llm import LLMMessage


def build_draft_messages(clean_text: str) -> list[LLMMessage]:
    """为“生成草稿”这个业务步骤组装聊天消息。

    LangGraph 节点和 LLM 适配层都不需要关心具体提示词：
    - 节点只知道要生成草稿
    - llm.py 只知道要发送 messages
    - 本函数专门负责表达“草稿应该怎么写”
    """

    return [
        {
            "role": "system",
            "content": (
                "你是 InkFlow 的内容草稿助手。"
                "请根据用户提供的输入，生成结构清晰的 Markdown 草稿。"
                "输出需要适合后续人工审核，不要声称已经发布。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请根据下面的输入内容生成一份 Markdown 草稿，包含：\n"
                "1. 标题\n"
                "2. 摘要\n"
                "3. 大纲\n"
                "4. 正文草稿\n"
                "5. 后续人工可补充的要点\n\n"
                f"输入内容：\n{clean_text}"
            ),
        },
    ]
