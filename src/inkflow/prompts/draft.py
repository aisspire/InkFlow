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
                "请根据用户提供的输入，生成结构清晰的 MDX 文件。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请根据下面的输入内容生成一份 MDX 文件，要求如下：\n"
                """
文章模板：
```mdx
---
title: '文章标题'
description: '一句话摘要，会用于文章列表、SEO、RSS 和社交分享描述'
date: 2026-05-18
tags: ['Astro', '学习笔记']
image: './banner.png'
authors: ['huijue']
draft: false
---

# 文章标题

这里开始写正文。
```
相关字段如下：
| 字段 | 类型 | 必填 | 作用与规范 |
| --- | --- | --- | --- |
| `title` | `string` | 是 | 文章标题，会出现在文章页、列表卡片、浏览器标题和社交分享标题中。建议清晰具体，不要只写“记录一下”。 |
| `description` | `string` | 是 | 文章摘要，会出现在文章卡片、SEO meta、RSS 和 Open Graph 描述中。建议 40-120 个中文字符，说明文章解决什么问题或记录什么内容。 |
| `date` | `date` | 是 | 发布时间，推荐使用 `YYYY-MM-DD`，例如 `2026-05-18`。主文章列表按日期倒序排列。 |
| `order` | `number` | 否 | 子文章排序辅助字段。子文章会先按 `date` 升序，再按 `order` 升序排列。普通主文章一般不用写。 |
| `image` | `image()` | 否 | 文章封面图。推荐放在文章目录下并写相对路径，例如 `./banner.png`。有封面时会显示在文章卡片和文章页顶部；没有时社交分享会回退到 `/static/1200x630.png`。 |
| `tags` | `string[]` | 否 | 标签数组，用于文章卡片和 `/tags` 页面。建议同类标签保持同一种写法，例如统一用 `Java`、`Astro`、`项目复盘`，不要一会儿写 `AI` 一会儿写 `ai`。 |
| `authors` | `string[]` | 否 | 作者 ID 数组，对应 `src/content/authors` 下的文件名。例如 `src/content/authors/huijue.md` 对应 `authors: ['huijue']`。未匹配到作者时会用 ID 作为名称并回退默认头像。 |
| `draft` | `boolean` | 否 | 是否为草稿。`true` 时不会出现在文章列表、RSS、标签页，也不会生成文章路由；发布时改为 `false` 或删除该字段。 |

可以使用MDX能力

文章默认使用 `.mdx` 时，可以写 Markdown，也可以引入组件。

代码块支持标题、行号等 Expressive Code 参数：

````mdx
```ts title="src/example.ts" startLineNumber={10}
export const hello = 'world'
```
````

如果不想显示行号：

````mdx
```bash showLineNumbers={false}
npm run build
```
````

数学公式由 `remark-math` 和 `rehype-katex` 支持：

```mdx
行内公式：$a^2 + b^2 = c^2$

块级公式：

$$
E = mc^2
$$
```

提示块可以使用 `src/components/callout.astro`：

```mdx
import Callout from '@/components/callout.astro'

<Callout variant="tip">
  这里写提示内容。
</Callout>
```

常用 `variant` 包括 `note`、`tip`、`warning`、`danger`、`important`、`definition`、`theorem`、`proof`、`example`、`exercise`、`problem` 等。

                """
                f"输入内容：\n{clean_text}"
            ),
        },
    ]
