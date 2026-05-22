"""InkFlow 的最小 LangGraph 工作流。

这个文件会刻意保持简单，当前包含四个节点：

1. preprocess_node：清洗输入文本。
2. redaction_plan_node：生成脱敏审查方案。
3. redaction_review_node：让用户逐条确认脱敏方案。
4. draft_node：生成一个占位草稿。
5. review_node：把结果标记为等待人工审核。

后续可以逐步把这些占位逻辑替换成真正的 LLM、RAG 和审核逻辑。
"""

from langgraph.graph import END, START, StateGraph

from inkflow.services.audit import append_audit_event
from inkflow.services.console_review import review_redaction_findings
from inkflow.services.draft import build_placeholder_draft, generate_draft
from inkflow.services.redaction import generate_redaction_findings
from inkflow.state import InkFlowState


def preprocess_node(state: InkFlowState) -> dict:
    """在生成草稿前清洗原始输入。

    LangGraph 的节点本质上就是一个 Python 函数。
    它接收当前状态，然后返回自己想更新的字段。
    """

    raw_text = state["raw_text"]
    warnings = list(state.get("warnings", []))

    # 第一版先保持简单：去掉首尾空白，并处理空输入。
    clean_text = raw_text.strip()
    if not clean_text:
        warnings.append("输入内容为空，后续草稿只能生成占位内容。")
        clean_text = "（空输入）"

    # 这里先放一个很小的敏感信息替换示例。
    # 后续可以把它升级成独立的规则列表或正则模块。
    clean_text = clean_text.replace("密码", "***")
    clean_text = clean_text.replace("password", "***")

    return {
        "clean_text": clean_text,
        "warnings": warnings,
    }


def draft_node(state: InkFlowState) -> dict:
    """根据清洗后的文本生成第一版草稿。

    节点只负责从状态里读取 clean_text，并把生成结果写回 draft。
    至于草稿是由真实 LLM 生成，还是降级为本地临时草稿，
    这些业务细节交给 draft.py 处理。
    """

    clean_text = state["clean_text"]
    warnings = list(state.get("warnings", []))

    try:
        draft = generate_draft(clean_text)
    except Exception as error:
        warnings.append(f"草稿生成失败，已降级为本地临时草稿：{error}")
        draft = build_placeholder_draft(clean_text)

    return {
        "draft": draft,
        "warnings": warnings,
    }


def redaction_plan_node(state: InkFlowState) -> dict:
    """生成脱敏审查方案。

    这个节点先不做终端交互，只把 LLM 发现的敏感项写入状态。
    后续 Task 4 会新增人工逐条确认节点，再决定是否真的执行脱敏。
    """

    text = state.get("redacted_text") or state.get("clean_text") or state["raw_text"]
    findings = generate_redaction_findings(text)

    # 先把 findings 打印出来，方便当前阶段手动观察 LLM 或本地降级结果。
    print("=== Redaction Findings ===")
    print(findings)

    return {
        "redaction_findings": findings,
        "audit_events": append_audit_event(
            state.get("audit_events"),
            "redaction_plan",
            "llm_findings",
            {"findings": findings},
        ),
    }


def redaction_review_node(state: InkFlowState) -> dict:
    """让用户在终端里逐条审查脱敏方案。

    这个节点只收集用户决策，不直接改写原文。
    真正的脱敏执行会在后续节点里根据 redaction_decisions 完成。
    """

    status, decisions = review_redaction_findings(state.get("redaction_findings", []))
    if status == "stop":
        review_status = "stopped_by_user"
    elif decisions:
        review_status = "pending_redaction"
    else:
        review_status = "redaction_reviewed"

    return {
        "review_status": review_status,
        "redaction_decisions": decisions,
        "audit_events": append_audit_event(
            state.get("audit_events"),
            "redaction_review",
            "user_decisions",
            {"status": review_status, "decisions": decisions},
        ),
    }


def route_after_redaction_review(state: InkFlowState) -> str:
    """根据人工审查结果决定是否继续生成草稿。

    Task 5 会新增真正的 apply_redaction 节点；在它接入前，
    如果已经有需要执行的脱敏决策，先停在 pending_redaction，
    避免未脱敏文本继续进入草稿生成。
    """

    if state.get("review_status") in {"stopped_by_user", "pending_redaction"}:
        return "stop"
    return "draft"


def review_node(state: InkFlowState) -> dict:
    """把工作流标记为等待人工审核。

    README 里设计了人工审核节点。这里先只用一个状态字段表达：
    程序已经生成草稿，但还没有自动发布。
    """

    return {"review_status": "pending_human_review"}


def build_graph():
    """构建并编译 LangGraph 工作流。

    StateGraph 用来描述工作流结构。
    compile() 会把这个结构变成可以 invoke() 执行的应用。
    """

    graph = StateGraph(InkFlowState)

    # 把每个 Python 函数注册成一个带名字的图节点。
    graph.add_node("preprocess", preprocess_node)
    graph.add_node("redaction_plan", redaction_plan_node)
    graph.add_node("redaction_review", redaction_review_node)
    graph.add_node("draft", draft_node)
    graph.add_node("review", review_node)

    # 定义状态在图里的流转路线。
    graph.add_edge(START, "preprocess")
    graph.add_edge("preprocess", "redaction_plan")
    graph.add_edge("redaction_plan", "redaction_review")
    graph.add_conditional_edges(
        "redaction_review",
        route_after_redaction_review,
        {
            "stop": END,
            "draft": "draft",
        },
    )
    graph.add_edge("draft", "review")
    graph.add_edge("review", END)

    return graph.compile()
