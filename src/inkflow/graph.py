"""Minimal LangGraph workflow for InkFlow.

This file intentionally keeps the graph small:

1. preprocess_node: clean input text.
2. draft_node: create a placeholder draft.
3. review_node: mark the result as waiting for human review.

Later steps can replace each placeholder with real LLM, RAG, and review logic.
"""

from langgraph.graph import END, START, StateGraph

from inkflow.state import InkFlowState


def preprocess_node(state: InkFlowState) -> dict:
    """Clean the original input before drafting.

    A LangGraph node is just a Python function.
    It receives the current state and returns the fields it wants to update.
    """

    raw_text = state["raw_text"]
    warnings = list(state.get("warnings", []))

    # Keep the first version simple: trim whitespace and normalize empty input.
    clean_text = raw_text.strip()
    if not clean_text:
        warnings.append("输入内容为空，后续草稿只能生成占位内容。")
        clean_text = "（空输入）"

    # A tiny example of sensitive information masking.
    # Later this can become a dedicated rule list or regex module.
    clean_text = clean_text.replace("密码", "***")
    clean_text = clean_text.replace("password", "***")

    return {
        "clean_text": clean_text,
        "warnings": warnings,
    }


def draft_node(state: InkFlowState) -> dict:
    """Create a first draft from cleaned text.

    This node does not call an LLM yet. For learning LangGraph, it is better
    to first understand the graph shape, then plug real model calls into nodes.
    """

    clean_text = state["clean_text"]

    draft = (
        "# InkFlow 临时草稿\n\n"
        "## 输入摘要\n\n"
        f"{clean_text}\n\n"
        "## 下一步\n\n"
        "这里之后会接入 LLM：根据输入生成摘要、大纲和正文。"
    )

    return {"draft": draft}


def review_node(state: InkFlowState) -> dict:
    """Mark the workflow as waiting for human review.

    README 里设计了人工审核节点。这里先只用一个状态字段表达：
    程序已经生成草稿，但还没有自动发布。
    """

    return {"review_status": "pending_human_review"}


def build_graph():
    """Build and compile the LangGraph workflow.

    StateGraph describes the workflow structure.
    compile() turns that structure into something we can invoke.
    """

    graph = StateGraph(InkFlowState)

    # Register each Python function as a named graph node.
    graph.add_node("preprocess", preprocess_node)
    graph.add_node("draft", draft_node)
    graph.add_node("review", review_node)

    # Define the route through the graph.
    graph.add_edge(START, "preprocess")
    graph.add_edge("preprocess", "draft")
    graph.add_edge("draft", "review")
    graph.add_edge("review", END)

    return graph.compile()
