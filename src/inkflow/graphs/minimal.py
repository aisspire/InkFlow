"""InkFlow 的最小 LangGraph 工作流。

这个文件会刻意保持简单，当前包含四个节点：

1. preprocess_node：清洗输入文本。
2. redaction_plan_node：生成脱敏审查方案。
3. redaction_review_node：让用户逐条确认脱敏方案。
4. apply_redaction_node：执行用户确认的脱敏修改并展示 diff。
5. article_generation_node：生成结构化文章 JSON。
6. package_node：拼装 Astro Markdown。
7. write_review_node：写入本地审阅稿并收集用户选择。

后续可以逐步把这些占位逻辑替换成真正的 LLM、RAG 和审核逻辑。
"""

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from inkflow.services.audit import append_audit_event
from inkflow.services.article import build_placeholder_article, generate_article_data
from inkflow.services.console_review import confirm_redaction_diff, review_redaction_findings
from inkflow.services.draft import build_placeholder_draft, generate_draft
from inkflow.services.packaging import package_astro_markdown
from inkflow.services.redaction import (
    apply_redaction_with_llm,
    build_text_diff,
    generate_redaction_findings,
)
from inkflow.services.review_output import review_generated_draft, write_review_file
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

    # 如果前面已经完成脱敏，后续草稿必须优先使用 redacted_text，
    # 避免复查通过后又把原始 clean_text 带入生成阶段。
    clean_text = state.get("redacted_text") or state["clean_text"]
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
    """根据人工审查结果决定是否继续生成文章 JSON。

    如果有需要执行的脱敏决策，就进入 apply_redaction 节点；
    如果用户停止，则直接结束；如果没有要处理的敏感项，则继续生成文章数据。
    """

    if state.get("review_status") == "stopped_by_user":
        return "stop"
    if state.get("review_status") == "pending_redaction":
        return "apply_redaction"
    return "article_generation"


def apply_redaction_node(state: InkFlowState) -> dict:
    """执行用户确认的脱敏修改，并让用户确认 diff。

    这个节点内部允许多次重试：用户不满意本次 diff 时，可以输入额外建议，
    节点会把建议传给 LLM 再改一次。用户确认后，图会回到 redaction_plan 复查。
    """

    source_text = state.get("redacted_text") or state.get("clean_text") or state["raw_text"]
    decisions = state.get("redaction_decisions", [])
    audit_events = state.get("audit_events")
    warnings = list(state.get("warnings", []))
    extra_instruction = ""

    while True:
        try:
            next_text = apply_redaction_with_llm(
                source_text,
                decisions,
                extra_instruction=extra_instruction,
            )
        except Exception as error:
            warnings.append(f"脱敏执行失败，流程已停止等待人工处理：{error}")
            return {
                "review_status": "redaction_apply_failed",
                "warnings": warnings,
                "audit_events": append_audit_event(
                    audit_events,
                    "apply_redaction",
                    "llm_apply_failed",
                    {"error": str(error), "decisions": decisions},
                ),
            }

        diff = build_text_diff(source_text, next_text)
        action, user_note = confirm_redaction_diff(diff)

        audit_events = append_audit_event(
            audit_events,
            "apply_redaction",
            "user_diff_decision",
            {"action": action, "user_note": user_note, "diff": diff},
        )

        if action == "accept":
            return {
                "redacted_text": next_text,
                "redaction_diff": diff,
                "review_status": "redaction_applied",
                "audit_events": audit_events,
            }

        if action == "stop":
            return {
                "review_status": "stopped_by_user",
                "audit_events": audit_events,
            }

        extra_instruction = user_note or "用户不接受本次 diff，请重新按原始决策改写。"


def route_after_apply_redaction(state: InkFlowState) -> str:
    """脱敏执行后决定是复查还是结束。"""

    if state.get("review_status") == "redaction_applied":
        return "recheck"
    return "stop"


def article_generation_node(state: InkFlowState) -> dict:
    """生成结构化文章 JSON。

    这里优先使用 redacted_text，确保已经通过复查的脱敏文本进入写作阶段。
    如果 LLM 不可用或返回异常，则降级成本地占位文章，方便继续验证后续节点。
    """

    text = state.get("redacted_text") or state.get("clean_text") or state["raw_text"]
    warnings = list(state.get("warnings", []))
    audit_events = state.get("audit_events")
    extra_instruction = state.get("article_feedback", "")

    try:
        article_data, qa_history = generate_article_data(
            text,
            extra_instruction=extra_instruction,
        )
    except Exception as error:
        warnings.append(f"文章 JSON 生成失败，已降级为本地占位文章：{error}")
        article_data = build_placeholder_article(text)
        qa_history = []

    audit_events = append_audit_event(
        audit_events,
        "article_generation",
        "article_data_generated",
        {
            "article_data": article_data,
            "qa_history": qa_history,
            "extra_instruction": extra_instruction,
        },
    )

    return {
        "article_data": article_data,
        "article_feedback": "",
        "audit_events": audit_events,
        "warnings": warnings,
    }


def package_node(state: InkFlowState) -> dict:
    """把文章 JSON 拼装成 Astro Markdown。

    这个节点只做格式拼装，不再调用 LLM。
    这样 frontmatter 字段、引号转义和正文边界都由程序稳定控制。
    """

    article_data = state["article_data"]
    final_document = package_astro_markdown(article_data)

    print("=== Astro Markdown ===")
    print(final_document)

    return {
        "final_document": final_document,
        "audit_events": append_audit_event(
            state.get("audit_events"),
            "package",
            "astro_markdown_packaged",
            {"final_document": final_document},
        ),
    }


def write_review_node(state: InkFlowState) -> dict:
    """写入本地审阅稿，并根据用户选择决定下一段流转。

    Task 8 只负责“生成审阅稿”和“人工决定下一步”。
    用户接受后先停在 accepted_for_publish，真正发布节点会在 Task 9 接上。
    """

    article_data = state["article_data"]
    final_document = state["final_document"]
    review_dir = _resolve_review_dir(state.get("config", {}))
    review_path = write_review_file(final_document, article_data["title"], review_dir)
    action, feedback = review_generated_draft(review_path)

    approved = action == "accepted"
    if action == "accepted":
        review_status = "accepted_for_publish"
    elif action == "redact_again":
        review_status = "review_returned_to_redaction"
    elif action == "regenerate":
        review_status = "review_returned_to_generation"
    else:
        review_status = "stopped_by_user"

    return {
        "review_path": str(review_path),
        "review_action": action,
        "article_feedback": feedback,
        "approved": approved,
        "review_status": review_status,
        "audit_events": append_audit_event(
            state.get("audit_events"),
            "write_review",
            "user_review_action",
            {
                "review_path": str(review_path),
                "action": action,
                "feedback": feedback,
            },
        ),
    }


def route_after_write_review(state: InkFlowState) -> str:
    """根据审阅选择决定回到哪一个节点。

    accepted 目前先结束，Task 9 会把它接到发布节点；
    regenerate 会携带 article_feedback 回到文章生成节点；
    redact_again 会回到脱敏方案节点重新审查。
    """

    action = state.get("review_action")
    if action == "redact_again":
        return "redact_again"
    if action == "regenerate":
        return "regenerate"
    return "stop"


def _resolve_review_dir(config: dict) -> Path:
    """从配置中解析审阅稿目录。

    config.toml 里的相对路径以配置文件所在目录为基准。
    main.py 会把这个目录写入 _config_dir，避免节点重新读取配置文件。
    """

    review_config = config.get("review", {})
    if not isinstance(review_config, dict):
        review_config = {}

    review_dir = Path(str(review_config.get("dir", "reviews")))
    if review_dir.is_absolute():
        return review_dir

    config_dir = Path(str(config.get("_config_dir", ".")))
    return config_dir / review_dir


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
    graph.add_node("apply_redaction", apply_redaction_node)
    graph.add_node("article_generation", article_generation_node)
    graph.add_node("package", package_node)
    graph.add_node("draft", draft_node)
    graph.add_node("write_review", write_review_node)

    # 定义状态在图里的流转路线。
    graph.add_edge(START, "preprocess")
    graph.add_edge("preprocess", "redaction_plan")
    graph.add_edge("redaction_plan", "redaction_review")
    graph.add_conditional_edges(
        "redaction_review",
        route_after_redaction_review,
        {
            "stop": END,
            "apply_redaction": "apply_redaction",
            "article_generation": "article_generation",
        },
    )
    graph.add_conditional_edges(
        "apply_redaction",
        route_after_apply_redaction,
        {
            "recheck": "redaction_plan",
            "stop": END,
        },
    )
    graph.add_edge("article_generation", "package")
    graph.add_edge("package", "write_review")
    graph.add_conditional_edges(
        "write_review",
        route_after_write_review,
        {
            "redact_again": "redaction_plan",
            "regenerate": "article_generation",
            "stop": END,
        },
    )

    return graph.compile()
