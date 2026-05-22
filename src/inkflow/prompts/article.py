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

    return [
        {
            "role": "system",
            "content": (
                "你是 InkFlow 的博客文章生成助手。"
                "你需要把用户笔记整理成适合 Astro 静态博客的文章 JSON。"
                "只返回 JSON 对象，不要返回 Markdown 代码块、解释或额外文本。"
            ),
        },
        {
            "role": "user",
            "content": f"""请基于下方正文生成文章 JSON。

你只能返回下面两种 JSON 结构之一。

如果还缺少关键写作方向，请返回：
{{
  "needs_user_input": true,
  "question": "需要询问用户的问题"
}}

如果信息足够，请返回：
{{
  "needs_user_input": false,
  "article": {{
    "title": "文章标题",
    "slug": "article-url-slug",
    "description": "文章描述",
    "date": "{today}",
    "tags": ["标签"],
    "authors": ["huijue"],
    "draft": false,
    "body": "Markdown 正文"
  }}
}}

要求：
- date 使用 YYYY-MM-DD 格式；如果没有明确发布日期，使用 {today}。
- slug 是发布路径里的文件夹名，只能使用小写英文、数字和连字符 -，不要使用中文、空格或下划线。
- slug 要朴素直接，优先用 3 到 8 个英文单词表达主题，例如 langgraph-content-workflow。
- body 必须是完整 Markdown 正文，不要包含 frontmatter。
- 返回 JSON 字符串时必须正确转义反斜杠，例如 Windows 路径里的 \\ 要写成 \\\\。
- tags 可以为空数组，最终 frontmatter 会拼成 `tags: ['tag1', 'tag2']` 这种单行格式。
- authors 必须返回，默认固定为 ["huijue"]。
- 文章应适合公开发布，不要重新引入脱敏前的敏感信息。

用户额外修改建议：
{instruction_text}

历史问答：
{history_text}

正文：
{text}
""",
        },
    ]


def _format_qa_history(qa_history: list[dict[str, str]]) -> str:
    """把模型追问和用户回答整理成 prompt 中容易阅读的文本。"""

    if not qa_history:
        return "无"

    return "\n".join(
        f"- 问：{item.get('question', '')}\n  答：{item.get('answer', '')}"
        for item in qa_history
    )
