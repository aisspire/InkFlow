"""Astro Markdown 拼装服务。

模型负责生成结构化文章内容；本模块负责把结构化数据稳定地写成 Markdown。
这样可以避免 LLM 自由发挥 frontmatter 格式，后续发布和报告也更容易追踪。
"""

from inkflow.state import ArticleData


def package_astro_markdown(article: ArticleData) -> str:
    """把文章 JSON 拼装成 Astro 可读取的 Markdown 文件。

    当前项目的 ArticleData 使用 ``date`` 字段，对应 Astro frontmatter 中的日期。
    tags 和 authors 使用 Astro 模板当前期望的 inline array 格式。
    """

    frontmatter_lines = [
        "---",
        f'title: "{_escape_frontmatter_string(article["title"])}"',
        f'description: "{_escape_frontmatter_string(article["description"])}"',
        f'date: "{_escape_frontmatter_string(article["date"])}"',
    ]

    frontmatter_lines.append(f"tags: {_format_inline_array(article.get('tags', []))}")
    frontmatter_lines.append(
        f"authors: {_format_inline_array(article.get('authors') or ['huijue'])}"
    )
    frontmatter_lines.append(f"draft: {str(article.get('draft', False)).lower()}")
    frontmatter_lines.append("---")

    body = article["body"].strip()
    return "\n".join(frontmatter_lines) + "\n\n" + body + "\n"


def _escape_frontmatter_string(value: object) -> str:
    """对 frontmatter 双引号字符串做最小转义。"""

    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _format_inline_array(values: list[object]) -> str:
    """把列表拼成 frontmatter 里的单行数组。"""

    if not values:
        return "[]"
    return "[" + ", ".join(f"'{_escape_inline_array_string(value)}'" for value in values) + "]"


def _escape_inline_array_string(value: object) -> str:
    """对单引号数组元素做最小转义。"""

    return str(value).replace("\\", "\\\\").replace("'", "\\'")
