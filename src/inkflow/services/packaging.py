"""Astro Markdown 拼装服务。

模型负责生成结构化文章内容；本模块负责把结构化数据稳定地写成 Markdown。
这样可以避免 LLM 自由发挥 frontmatter 格式，后续发布和报告也更容易追踪。
"""

from inkflow.state import ArticleData


def package_astro_markdown(article: ArticleData) -> str:
    """把文章 JSON 拼装成 Astro 可读取的 Markdown 文件。

    当前项目的 ArticleData 使用 ``date`` 字段，对应 Astro frontmatter 中的日期。
    tags 为空时输出 ``tags: []``，避免 YAML 结构在空列表时变得含糊。
    """

    frontmatter_lines = [
        "---",
        f'title: "{_escape_frontmatter_string(article["title"])}"',
        f'description: "{_escape_frontmatter_string(article["description"])}"',
        f'date: "{_escape_frontmatter_string(article["date"])}"',
    ]

    tags = article.get("tags", [])
    if tags:
        frontmatter_lines.append("tags:")
        for tag in tags:
            frontmatter_lines.append(f'  - "{_escape_frontmatter_string(tag)}"')
    else:
        frontmatter_lines.append("tags: []")

    frontmatter_lines.append(f"draft: {str(article.get('draft', False)).lower()}")
    frontmatter_lines.append("---")

    body = article["body"].strip()
    return "\n".join(frontmatter_lines) + "\n\n" + body + "\n"


def _escape_frontmatter_string(value: object) -> str:
    """对 frontmatter 双引号字符串做最小转义。"""

    return str(value).replace("\\", "\\\\").replace('"', '\\"')
