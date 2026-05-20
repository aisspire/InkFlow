"""InkFlow 的草稿生成业务模块。

这个模块负责“草稿怎么生成”：
- LLM 开启时，组装草稿提示词并调用真实模型
- LLM 未开启时，返回本地临时草稿

它位于 graph.py 和 llm.py 中间，让工作流节点不用知道 prompt，
也让底层 LLM 适配层不用知道“草稿”这个业务概念。
"""

from pathlib import Path

from inkflow.llm import DEFAULT_LLM_CONFIG_PATH, call_llm, is_llm_enabled, load_llm_config
from inkflow.prompts import build_draft_messages


def generate_draft(
    clean_text: str,
    *,
    config_path: Path = DEFAULT_LLM_CONFIG_PATH,
) -> str:
    """根据清洗后的输入文本生成 Markdown 草稿。

    这里是草稿生成的业务入口。后续如果草稿生成拆成
    “摘要 -> 大纲 -> 正文”多个内部步骤，可以先在这个函数里组织；
    等这些中间结果需要进入 LangGraph 状态时，再拆成多个图节点。
    """

    if not is_llm_enabled(config_path):
        return build_placeholder_draft(clean_text)

    config = load_llm_config(config_path)
    messages = build_draft_messages(clean_text)
    return call_llm(messages, config=config)


def build_placeholder_draft(clean_text: str) -> str:
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
