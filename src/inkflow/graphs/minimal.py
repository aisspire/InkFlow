"""Web 与 CLI 共用的 LangGraph 工作流。

每个模型调用、人工 interrupt 和文件写入都有独立节点。恢复 interrupt 时，
LangGraph 会重入等待节点，但不会重做前一个节点的模型调用或文件写入。
节点只返回状态增量；真实节点和条件边也直接用于前端流程图。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from inkflow.actions import validate_action
from inkflow.services.article import build_placeholder_article, generate_article_data
from inkflow.services.audit import append_audit_event
from inkflow.services.local_files import atomic_write
from inkflow.services.packaging import package_astro_markdown
from inkflow.services.publish import preview_blog_target, write_final_blog_article
from inkflow.services.redaction import apply_redaction_with_llm, build_text_diff, generate_redaction_findings
from inkflow.services.report import write_audit_jsonl, write_markdown_report
from inkflow.settings import SettingsStore, resolve_directory
from inkflow.state import InkFlowState
from inkflow.runtime import WorkflowRuntime


# 布局只描述展示位置，不允许浏览器编辑图。边来自实际编译的图。
NODE_META = {
    "preprocess": ("预处理", "文章工作台保留原文；兼容 CLI 的旧清洗规则。", 0, 0),
    "redaction_plan": ("脱敏方案", "分析敏感内容，生成带行号的审核建议。", 1, 0),
    "redaction_review": ("人工审核", "逐项采用、忽略或自定义修改方案。", 2, 0),
    "apply_redaction": ("执行改写", "只按已确认方案改写，一次调用对应一次执行。", 2, 1),
    "diff_review": ("确认差异", "查看改写 diff；接受后复查，也可以补充建议重试。", 1, 1),
    "article_generation": ("文章生成", "生成结构化文章；失败会明确标记占位结果。", 0, 1),
    "article_question": ("补充信息", "回答模型追问，每轮生成最多三次。", 0, 2),
    "package": ("Markdown 拼装", "统一拼装元数据和正文，产生新的文章版本。", 1, 2),
    "write_review": ("保存审阅稿", "落盘当前文档并计算博客目标与覆盖信息。", 2, 2),
    "final_review": ("编辑与审阅", "显式保存编辑后可结束、写入博客或退回上游。", 2, 3),
    "publish": ("写入博客", "核对目标后仅写入 <slug>/index.mdx。", 1, 3),
    "report": ("生成报告", "保存 Markdown 报告与完整 JSONL 审计记录。", 0, 3),
}

# 所有节点都使用条件边，包括停止和失败出口；避免 Command 与静态边并行推进。
ROUTES = {
    "preprocess": ["redaction_plan", "report"],
    "redaction_plan": ["redaction_review", "report"],
    "redaction_review": ["apply_redaction", "article_generation", "report"],
    "apply_redaction": ["diff_review", "report"],
    "diff_review": ["redaction_plan", "apply_redaction", "report"],
    "article_generation": ["article_question", "package", "report"],
    "article_question": ["article_generation", "report"],
    "package": ["write_review", "report"],
    "write_review": ["final_review", "report"],
    "final_review": ["package", "publish", "redaction_plan", "article_generation", "report"],
    "publish": ["report"],
    "report": [END],
}

INPUT_FIELDS = {
    "preprocess": ["raw_text"], "redaction_plan": ["clean_text", "redacted_text"],
    "redaction_review": ["redaction_findings"],
    "apply_redaction": ["redacted_text", "clean_text", "redaction_decisions", "redaction_instruction"],
    "diff_review": ["candidate_text", "redaction_diff"],
    "article_generation": ["redacted_text", "clean_text", "article_feedback", "qa_history"],
    "article_question": ["question", "qa_history"], "package": ["article_data"],
    "write_review": ["final_document", "revision"],
    "final_review": ["article_data", "revision", "publish_preview"],
    "publish": ["final_document", "publish_preview", "approved"],
    "report": ["review_status", "review_path", "blog_path", "warnings", "failure"],
}


def _source(state: InkFlowState) -> str:
    if "redacted_text" in state:
        return state["redacted_text"]
    return state.get("clean_text", state["raw_text"])


def _stopped() -> dict:
    return {"stopped": True, "approved": False, "review_status": "stopped_by_user", "next_node": "report"}


def _wait(kind: str, **data) -> dict:
    payload = {"kind": kind, "node": kind, **data}
    # interrupt 必须原样抛给 LangGraph，不能被普通的错误降级逻辑吞掉。
    response = interrupt(payload)
    return validate_action(payload, response["action"], response.get("payload", {}))


def preprocess_node(state: InkFlowState) -> dict:
    clean = state["raw_text"] if state.get("editor_bound") else state["raw_text"].strip().replace("密码", "***").replace("password", "***")
    return {"clean_text": clean or "（空输入）", "run_id": state.get("run_id") or uuid4().hex,
            "approved": False, "review_status": "processing", "next_node": "redaction_plan"}


def redaction_review_node(state: InkFlowState) -> dict:
    choice = _wait("redaction_review", findings=state.get("redaction_findings", []),
                   llm_enabled=state.get("config", {}).get("llm", {}).get("enabled", False))
    if choice["action"] == "stop":
        return _stopped()
    decisions = choice["decisions"]
    return {"redaction_decisions": decisions, "redaction_instruction": "",
            "next_node": "apply_redaction" if any(x["action"] == "apply" for x in decisions) else "article_generation"}


def diff_review_node(state: InkFlowState) -> dict:
    choice = _wait("diff_review", diff=state["redaction_diff"], candidate_text=state["candidate_text"])
    if choice["action"] == "stop":
        return _stopped()
    if choice["action"] == "retry":
        return {"redaction_instruction": choice["feedback"], "next_node": "apply_redaction"}
    return {"redacted_text": state["candidate_text"], "next_node": "redaction_plan"}


def article_question_node(state: InkFlowState) -> dict:
    choice = _wait("article_question", question=state["question"],
                   question_number=len(state.get("qa_history", [])) + 1)
    if choice["action"] == "stop":
        return _stopped()
    return {"qa_history": [*state.get("qa_history", []),
                           {"question": state["question"], "answer": choice["answer"]}],
            "question": "", "next_node": "article_generation"}


def package_node(state: InkFlowState) -> dict:
    return {"final_document": package_astro_markdown(state["article_data"]),
            "revision": state.get("revision", 0) + 1, "approved": False, "next_node": "write_review"}


def _directory(state: InkFlowState, name: str) -> Path:
    config = state.get("config", {})
    return resolve_directory(config.get(name, {}).get("dir", f"{name}s"), Path(config.get("_config_dir", ".")))


def _blog(state: InkFlowState) -> tuple[Path, str]:
    config = state.get("config", {})
    blog = config.get("blog", {})
    if not blog.get("repo_path"):
        raise ValueError("未设置博客仓库路径；可先保存审阅稿并结束。")
    return (resolve_directory(blog["repo_path"], Path(config.get("_config_dir", "."))),
            blog.get("content_dir", "src/content/blog"))


def write_review_node(state: InkFlowState) -> dict:
    path = _directory(state, "review") / f"{state['run_id']}.md"
    atomic_write(path, state["final_document"])
    try:
        repo, content = _blog(state)
        preview = preview_blog_target(repo, content, state["article_data"].get("slug") or state["article_data"]["title"])
    except (ValueError, OSError) as error:
        # 博客未配置不能阻止编辑及保存审阅稿，但必须禁止确认写入。
        preview = {"error": str(error)}
    return {"review_path": str(path), "publish_preview": preview, "next_node": "final_review"}


def final_review_node(state: InkFlowState) -> dict:
    choice = _wait("final_review", article=state["article_data"], document=state["final_document"],
                   revision=state["revision"], publish_preview=state["publish_preview"],
                   review_path=state["review_path"], degraded=state.get("degraded", False),
                   no_publish=state.get("config", {}).get("publish", {}).get("no_publish", False))
    action = choice["action"]
    if action == "stop":
        return _stopped()
    if action == "save":
        return {"article_data": choice["article"], "approved": False, "next_node": "package", "review_action": "saved"}
    if action in {"regenerate", "redact_again"}:
        # 退回上游立刻清除最终确认，旧版本不能成为生产节点的输入。
        return {"approved": False, "final_document": "", "article_data": {}, "publish_preview": {},
                "qa_history": [], "question": "", "article_feedback": choice["feedback"],
                "review_action": action, "review_status": "processing",
                "next_node": "article_generation" if action == "regenerate" else "redaction_plan"}
    return {"approved": True, "review_action": action,
            "review_status": "accepted_no_publish" if action == "finish" else "accepted_for_publish",
            "next_node": "report" if action == "finish" else "publish"}


def publish_node(state: InkFlowState) -> dict:
    if not state.get("approved") or state.get("config", {}).get("publish", {}).get("no_publish"):
        raise ValueError("文章尚未确认，或本任务禁止博客写入。")
    repo, content = _blog(state)
    path = write_final_blog_article(state["final_document"], state["article_data"]["title"], repo, content,
                                    slug_source=state["article_data"].get("slug"), expected=state["publish_preview"])
    return {"blog_path": str(path), "review_status": "published", "next_node": "report"}


def report_node(state: InkFlowState) -> dict:
    report_dir = _directory(state, "report")
    run_id = state["run_id"]
    events = append_audit_event(state.get("audit_events"), "report", "report_requested",
                                {"review_status": state.get("review_status"), "run_id": run_id})
    snapshot = {**state, "audit_events": events}
    # 先生成 Markdown；即使第二份写入失败，管理器仍会保留此前已产生的记录。
    markdown = write_markdown_report(snapshot, report_dir, run_id)
    audit = write_audit_jsonl(events, report_dir, run_id)
    return {"report_path": str(markdown), "report_jsonl_path": str(audit), "next_node": END}


def _tracked(name: str, function: Callable, runtime: WorkflowRuntime):
    def node(state: InkFlowState) -> dict:
        writer = get_stream_writer()
        stopping_at_report = name == "report" and runtime.stop.is_set() and not state.get("failure")
        if stopping_at_report:
            # 写入博客后收到停止请求也要如实标记；已写文件不回滚。
            state = {**state, **_stopped()}
        writer(runtime.scrub({"type": "node_started", "node": name,
                              "input": {key: state[key] for key in INPUT_FIELDS[name] if key in state}}))
        if runtime.stop.is_set() and name != "report":
            writer({"type": "node_skipped", "node": name, "summary": "已在安全节点边界停止"})
            update = _stopped()
        else:
            try:
                update = function(state)
            except GraphInterrupt:
                raise
            except Exception as error:
                # 不把第三方原始异常（可能含请求/密钥）写入日志。
                message = str(error) if isinstance(error, (ValueError, OSError)) else f"执行失败（{type(error).__name__}），请检查设置后重新运行。"
                message = runtime.scrub(message)
                writer({"type": "node_failed", "node": name, "summary": message})
                update = {"failure": message, "approved": False, "review_status": f"{name}_failed",
                          "warnings": [*state.get("warnings", []), message],
                          "next_node": END if name == "report" else "report"}
        update = runtime.scrub(update)
        if stopping_at_report:
            update = {**_stopped(), **update}
        event_data = {key: value for key, value in update.items() if key not in {"audit_events", "next_node"}}
        update["audit_events"] = append_audit_event(state.get("audit_events"), name, "state_updated", event_data)
        return update
    return node


def build_graph(runtime: WorkflowRuntime | None = None):
    if runtime is None:
        _, llm, key = SettingsStore(Path("config.toml")).snapshot()
        runtime = WorkflowRuntime(llm, key)

    def plan(state: InkFlowState) -> dict:
        findings = generate_redaction_findings(_source(state), config=runtime.llm, api_key=runtime.api_key)
        return {"redaction_findings": findings, "checked_text": _source(state), "next_node": "redaction_review"}

    def apply(state: InkFlowState) -> dict:
        decisions = [x for x in state.get("redaction_decisions", []) if x["action"] == "apply"]
        candidate = apply_redaction_with_llm(_source(state), decisions, state.get("redaction_instruction", ""),
                                             config=runtime.llm, api_key=runtime.api_key, preserve_format=state.get('editor_bound', False))
        return {"candidate_text": candidate, "redaction_diff": build_text_diff(_source(state), candidate),
                "next_node": "diff_review"}

    def generate(state: InkFlowState) -> dict:
        warnings = list(state.get("warnings", []))
        try:
            result = generate_article_data(_source(state), extra_instruction=state.get("article_feedback", ""),
                                           qa_history=state.get("qa_history", []), config=runtime.llm, api_key=runtime.api_key)
        except Exception as error:
            warnings.append(f"文章生成失败（{type(error).__name__}），已使用本地占位文章；请人工审阅。")
            result = {"article": build_placeholder_article(_source(state)), "degraded": True}
        if "question" in result:
            return {"question": result["question"], "next_node": "article_question"}
        if not runtime.llm.enabled and "模型未启用，当前为本地占位文章。" not in warnings:
            warnings.append("模型未启用，当前为本地占位文章。")
        return {"article_data": result["article"], "degraded": result["degraded"], "warnings": warnings,
                "approved": False, "question": "", "next_node": "package"}

    functions = {"preprocess": preprocess_node, "redaction_plan": plan, "redaction_review": redaction_review_node,
                 "apply_redaction": apply, "diff_review": diff_review_node, "article_generation": generate,
                 "article_question": article_question_node, "package": package_node, "write_review": write_review_node,
                 "final_review": final_review_node, "publish": publish_node, "report": report_node}
    graph = StateGraph(InkFlowState)
    for name, function in functions.items():
        graph.add_node(name, _tracked(name, function, runtime))
    graph.add_edge(START, "preprocess")
    for name, destinations in ROUTES.items():
        graph.add_conditional_edges(name, lambda state: state["next_node"], {target: target for target in destinations})
    return graph.compile(checkpointer=InMemorySaver())


def workflow_description(graph) -> dict:
    drawable = graph.get_graph()
    return {
        "nodes": [{"id": name, "label": meta[0], "description": meta[1],
                   "position": {"x": meta[2] * 290, "y": meta[3] * 170},
                   "human": name in {"redaction_review", "diff_review", "article_question", "final_review"}}
                  for name, meta in NODE_META.items() if name in drawable.nodes],
        "edges": [{"id": f"{edge.source}-{edge.target}", "source": edge.source, "target": edge.target}
                  for edge in drawable.edges if edge.source in NODE_META and edge.target in NODE_META],
    }
