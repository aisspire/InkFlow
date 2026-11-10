"""Astro Markdown 拼装服务。

模型负责生成结构化文章内容；本模块负责把结构化数据稳定地写成 Markdown。
这样可以避免 LLM 自由发挥 frontmatter 格式，后续发布和报告也更容易追踪。
"""

import json
import re

from inkflow.state import ArticleData


def parse_generated_document(document: str, article: dict) -> dict:
    """读取本应用生成的 JSON 值 frontmatter；不充当通用 YAML 解析器。"""
    if not isinstance(document, str) or len(document) > 1_100_000:
        raise ValueError('请提交有效的工作流文章，正文最多 100 万字符。')
    match = re.match(r'\A\ufeff?---\r?\n(.*?)\r?\n---\r?\n([\s\S]*)\Z', document, re.DOTALL)
    if not match:
        raise ValueError('请保留工作流生成稿顶部的元数据区。')
    fields = {}
    required = {'title', 'description', 'date', 'tags', 'authors', 'draft'}
    for line in match[1].splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(':')
        if not separator or key not in required or key in fields:
            raise ValueError('元数据字段无效或重复，请使用生成稿原有字段，slug 可在文章属性中修改。')
        try:
            fields[key] = json.loads(value)
        except ValueError:
            raise ValueError('元数据字符串请使用双引号，列表保留方括号；也可在文章属性中编辑。') from None
    if fields.keys() != required:
        raise ValueError('生成稿的元数据字段不完整，请保留标题、摘要、日期、标签、作者和草稿标记。')
    return {**article, **fields, 'body': match[2]}


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

    # JSON 双引号转义也是合法 YAML，能处理换行而不引入额外字段。
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


def _format_inline_array(values: list[object]) -> str:
    """把列表拼成 frontmatter 里的单行数组。"""

    if not values:
        return "[]"
    return json.dumps([str(value) for value in values], ensure_ascii=False)


def _escape_inline_array_string(value: object) -> str:
    """对单引号数组元素做最小转义。"""

    return str(value).replace("\\", "\\\\").replace("'", "\\'")
