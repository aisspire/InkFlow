"""脱敏审查提示词。

这个模块只负责把“需要检查的文本”组装成 LLM messages。
真正的模型调用和 JSON 解析放在 services/redaction.py 中，避免 prompt 和业务执行混在一起。
"""

from inkflow.llm import LLMMessage


def build_redaction_messages(numbered_text: str) -> list[LLMMessage]:
    """为“生成脱敏方案”步骤组装聊天消息。"""

    return [
        {
            "role": "system",
            "content": (
                "你是 InkFlow 的内容脱敏审查助手。"
                "你的任务是找出不适合发布到公开博客的敏感信息、隐私信息、"
                "内部信息和明显不适合公开传播的内容。"
                "只返回 JSON 数组，不要返回 Markdown、解释文字或代码块。"
            ),
        },
        {
            "role": "user",
            "content": f"""请审查下面带行号的文本，返回 JSON 数组。

每个元素必须包含这些字段：
- id: 字符串，形如 S001、S002
- risk: "high"、"medium" 或 "low"
- line_start: 敏感内容起始行号
- line_end: 敏感内容结束行号
- original_excerpt: 原文片段
- issue: 问题类型
- reason: 为什么不适合公开发布
- suggestion: 建议替换或处理方案

风险定义：
- high: 密码、token、私有地址、内部系统、未公开项目、个人隐私、可导致攻击的信息。
- medium: 内部流程、架构细节、组织信息、未验证事实、可能带来间接风险的信息。
- low: 无关内容、错乱文本、重复段落、明显不适合发布的结构问题。

如果没有发现风险，请返回空数组 []。

带行号文本：
{numbered_text}
""",
        },
    ]
