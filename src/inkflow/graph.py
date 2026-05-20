"""InkFlow 的最小 LangGraph 工作流。

这个文件会刻意保持简单，只包含三个节点：

1. preprocess_node：清洗输入文本。
2. draft_node：生成一个占位草稿。
3. review_node：把结果标记为等待人工审核。

后续可以逐步把这些占位逻辑替换成真正的 LLM、RAG 和审核逻辑。
"""

from langgraph.graph import END, START, StateGraph

from inkflow.llm import LLMConfigError, generate_draft, is_llm_enabled
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

    这里已经接入了真实 LLM 的“可选分支”：
    - llm.toml 里 enabled = true 时，调用真实模型生成草稿
    - 未启用、未配置或调用失败时，降级为本地临时草稿

    这样既能学习 LangGraph 的节点写法，也能避免每次运行都意外调用付费模型。
    """

    clean_text = state["clean_text"]
    warnings = list(state.get("warnings", []))

    try:
        # is_llm_enabled() 只读取配置，不会发起网络请求。
        # 真正的 SDK 调用被封装在 generate_draft() 里，
        # draft_node 不需要知道 OpenAI 客户端、base_url 或 API Key 的细节。
        if is_llm_enabled():
            return {"draft": generate_draft(clean_text)}
    except LLMConfigError as error:
        warnings.append(f"LLM 配置不可用，已降级为本地临时草稿：{error}")
    except Exception as error:
        warnings.append(f"LLM 调用失败，已降级为本地临时草稿：{error}")

    return {
        "draft": _build_placeholder_draft(clean_text),
        "warnings": warnings,
    }


def _build_placeholder_draft(clean_text: str) -> str:
    """生成不依赖真实 LLM 的本地临时草稿。

    这个函数保留原来的占位行为，方便在没有 API Key、
    没有网络或不想消耗模型额度时继续运行完整工作流。
    """

    return (
        "# InkFlow 临时草稿\n\n"
        "## 输入摘要\n\n"
        f"{clean_text}\n\n"
        "## 下一步\n\n"
        "这里之后会接入 LLM：根据输入生成摘要、大纲和正文。"
    )


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
    graph.add_node("draft", draft_node)
    graph.add_node("review", review_node)

    # 定义状态在图里的流转路线。
    graph.add_edge(START, "preprocess")
    graph.add_edge("preprocess", "draft")
    graph.add_edge("draft", "review")
    graph.add_edge("review", END)

    return graph.compile()
