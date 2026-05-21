"""InkFlow LangGraph 工作流的共享状态定义。

LangGraph 会在节点之间传递同一个“状态”对象。
为了让第一版更适合学习，这里使用 TypedDict：

- 运行时它像普通 Python 字典一样使用。
- 编辑器和类型检查工具可以知道我们期望有哪些字段。
- 每个图节点都可以读取已有字段，并返回自己要更新的字段。
"""

from typing import Any, Literal, NotRequired, TypedDict


class RedactionFinding(TypedDict):
    """LLM 发现的一条敏感内容风险。

    这些字段会在后续“脱敏审查”节点中展示给用户，让用户逐条决定是否处理。
    """

    id: str
    risk: Literal["high", "medium", "low"]
    line_start: int
    line_end: int
    original_excerpt: str
    issue: str
    reason: str
    suggestion: str


class RedactionDecision(TypedDict):
    """用户对某条敏感内容风险做出的处理决定。

    action 只记录忽略或应用；真正替换时使用 user_instruction 作为最终方案。
    """

    finding_id: str
    action: Literal["ignore", "apply"]
    line_start: int
    line_end: int
    issue: str
    user_instruction: str


class ArticleData(TypedDict):
    """文章元数据和正文。

    这里的字段尽量贴近当前 Astro 博客模板的 frontmatter 规范：
    必填字段直接写成普通键，可选字段使用 NotRequired。
    """

    title: str
    description: str
    date: str
    body: str
    order: NotRequired[int]
    image: NotRequired[str]
    tags: NotRequired[list[str]]
    authors: NotRequired[list[str]]
    draft: NotRequired[bool]


class InkFlowState(TypedDict):
    """工作流中每个节点共享的状态。

    可以把它想成在流程里流转的“工单”：
    每个节点读取当前值，添加或修改少量字段，
    然后把状态继续交给下一个节点。
    """

    # 原始输入文本，未来可以来自用户、本地笔记、RSS 条目或 GitHub Release。
    raw_text: str

    # 输入文件路径，后续报告会用它追踪本次流程来自哪份笔记。
    source_path: NotRequired[str]

    # 经过基础清洗和简单敏感词替换后的文本。
    clean_text: NotRequired[str]

    # 脱敏审查阶段的数据：发现项、用户决策、脱敏后的文本和 diff。
    redaction_findings: NotRequired[list[RedactionFinding]]
    redaction_decisions: NotRequired[list[RedactionDecision]]
    redacted_text: NotRequired[str]
    redaction_diff: NotRequired[str]

    # 文章生成和 Astro Markdown 打包阶段的数据。
    article_data: NotRequired[ArticleData]
    final_document: NotRequired[str]
    review_path: NotRequired[str]
    approved: NotRequired[bool]

    # 发布阶段和报告阶段的数据。
    publish_path: NotRequired[str]
    publish_log: NotRequired[list[dict[str, Any]]]
    report_path: NotRequired[str]
    audit_events: NotRequired[list[dict[str, Any]]]

    # 文章草稿内容。第一版先用占位文本模拟，暂时不接入 LLM。
    draft: NotRequired[str]

    # 当前审核状态。后续可以用它驱动“人工介入”分支。
    review_status: NotRequired[str]

    # 流程中收集的非致命提示信息。
    warnings: NotRequired[list[str]]
