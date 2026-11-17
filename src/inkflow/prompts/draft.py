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

    from inkflow.workflow_assets.prompts import builtin_messages
    return builtin_messages('legacy-draft', {'clean_text': clean_text})
