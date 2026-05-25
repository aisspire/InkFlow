"""统一 InkFlow 的终端日志格式。

这里的函数只负责“怎么在终端提示用户”，不保存业务数据。
完整原文、diff 和 Markdown 仍然由报告、审阅稿等本地文件保存。
"""

from __future__ import annotations

from pathlib import Path

LOG_PREFIX = "[InkFlow]"


def log_section(title: str) -> None:
    """输出一个统一格式的日志分组标题。"""

    print(f"{LOG_PREFIX} {title}")


def log_item(label: str, value: object) -> None:
    """输出一条统一格式的键值日志。"""

    print(f"- {label}：{value}")


def log_message(message: str) -> None:
    """输出一条普通提示，保持和键值日志相同的列表样式。"""

    print(f"- {message}")


def log_hidden_content(label: str, *, path: Path | str | None = None) -> None:
    """提示长正文或敏感内容已隐藏。

    如果当前节点已经知道本地文件路径，就直接告诉用户去哪里看；
    如果路径还要等报告节点生成，就先提示流程结束后查看本地报告。
    """

    if path is None:
        log_item(label, "已隐藏，流程结束后请在本地报告文件中查看。")
        return

    log_item(label, f"已隐藏，请在本地文件中查看：{path}")


def log_llm_thinking() -> None:
    """每次真实调用 LLM 前提示用户需要等待。"""

    print(f"{LOG_PREFIX} 正在思考中…")
