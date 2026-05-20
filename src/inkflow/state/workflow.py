"""InkFlow LangGraph 工作流的共享状态定义。

LangGraph 会在节点之间传递同一个“状态”对象。
为了让第一版更适合学习，这里使用 TypedDict：

- 运行时它像普通 Python 字典一样使用。
- 编辑器和类型检查工具可以知道我们期望有哪些字段。
- 每个图节点都可以读取已有字段，并返回自己要更新的字段。
"""

from typing import NotRequired, TypedDict


class InkFlowState(TypedDict):
    """工作流中每个节点共享的状态。

    可以把它想成在流程里流转的“工单”：
    每个节点读取当前值，添加或修改少量字段，
    然后把状态继续交给下一个节点。
    """

    # 原始输入文本，未来可以来自用户、本地笔记、RSS 条目或 GitHub Release。
    raw_text: str

    # 经过基础清洗和简单敏感词替换后的文本。
    clean_text: NotRequired[str]

    # 文章草稿内容。第一版先用占位文本模拟，暂时不接入 LLM。
    draft: NotRequired[str]

    # 当前审核状态。后续可以用它驱动“人工介入”分支。
    review_status: NotRequired[str]

    # 流程中收集的非致命提示信息。
    warnings: NotRequired[list[str]]
