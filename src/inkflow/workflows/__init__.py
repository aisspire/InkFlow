"""小型内置工作流注册表；编辑器不依赖任何具体流程。"""

from dataclasses import dataclass
import hashlib
from typing import Callable, Any

from inkflow.runtime import WorkflowRuntime

DEFAULT_WORKFLOW_ID = "reviewed-publishing"


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    name: str
    description: str
    build_graph: Callable[[WorkflowRuntime], Any]
    describe_graph: Callable[[Any], dict]
    validate_action: Callable[[dict, str, dict], Any]
    document_result: Callable[[dict], dict | None] = lambda state: None

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description}

    def describe(self, graph=None) -> dict:
        if graph is None:
            from inkflow.llm import LLMConfig
            graph = self.build_graph(WorkflowRuntime(LLMConfig(False, "openai-compatible", "offline", "OPENAI_API_KEY")))
        return {**self.summary(), **self.describe_graph(graph)}


def _builtin() -> WorkflowDefinition:
    # 内置流程的接线集中在注册表，通用运行管理器不引用具体节点。
    from inkflow.actions import validate_action
    from inkflow.graphs.minimal import build_graph, workflow_description
    return WorkflowDefinition(
        DEFAULT_WORKFLOW_ID, "文章处理与审阅", "脱敏处理、人工审核、文章生成、审阅及博客输出。",
        build_graph, workflow_description, validate_action, _publishing_document,
    )


def _publishing_document(state: dict) -> dict | None:
    if state.get("final_document") and state.get("article_data"):
        text, kind, label = state["final_document"], "article", "文章审阅稿"
    elif state.get("redacted_text"):
        text, kind, label = state["redacted_text"], "redaction", "已确认脱敏稿"
    else:
        return None
    version = state.get("revision", 0) if kind == "article" else 0
    result_id = hashlib.sha256(f"{kind}:{version}:{text}".encode()).hexdigest()
    return {"id": result_id, "text": text, "kind": kind, "label": label}


def _full_check():
    from inkflow.workflows.full_check import build_graph, describe, validate_action
    return WorkflowDefinition('full-check', '全文检查', '表达优化、结构建议与一致性检查，人工结束本轮。',
                              build_graph, describe, validate_action)


WORKFLOWS = {_definition.id: _definition for _definition in [_builtin(), _full_check()]}


def get_workflow(workflow_id: str = DEFAULT_WORKFLOW_ID) -> WorkflowDefinition:
    try:
        return WORKFLOWS[workflow_id]
    except KeyError:
        raise ValueError(f"工作流不存在：{workflow_id}") from None
