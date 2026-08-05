"""统一 InkFlow 的终端日志格式。

这里的函数只负责“怎么在终端提示用户”，不保存业务数据。
完整原文、diff 和 Markdown 仍然由报告、审阅稿等本地文件保存。
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from rich.console import Console
    from rich.text import Text
except ImportError:  # 依赖尚未安装时仍保留原来的纯文本终端能力。
    Console = None  # type: ignore[assignment,misc]
    Text = None  # type: ignore[assignment,misc]


LOG_PREFIX = "[InkFlow]"
_console = (
    Console(
        highlight=False,
        no_color=bool(os.environ.get("NO_COLOR")),
    )
    if Console is not None
    else None
)


def _print_item(label: str, value: object) -> None:
    """使用 Rich 对齐键值；没有安装 Rich 时回退到原来的打印格式。"""

    if _console is None or Text is None:
        print(f"- {label}：{value}")
        return

    # 装饰符使用 ASCII，兼容尚未切换到 UTF-8 的旧版 Windows 控制台。
    line = Text("  > ", style="cyan")
    line.append(str(label), style="bold")
    line.append("  |  ", style="dim")
    line.append(str(value))
    _console.print(line)


def _print_message(message: str) -> None:
    """输出一条带视觉标记的普通提示，并兼容无 Rich 环境。"""

    if _console is None or Text is None:
        print(f"- {message}")
        return

    line = Text("  > ", style="cyan")
    line.append(message)
    _console.print(line)


def log_section(title: str) -> None:
    """输出一个带分隔线的日志分组标题。"""

    if _console is None or Text is None:
        print(f"{LOG_PREFIX} {title}")
        return

    _console.rule(
        Text(f" {LOG_PREFIX} - {title} ", style="bold cyan"),
        style="cyan",
    )


def log_item(label: str, value: object) -> None:
    """输出一条带颜色和分隔符的键值日志。"""

    _print_item(label, value)


def log_message(message: str) -> None:
    """输出一条普通提示。"""

    _print_message(message)


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

    if _console is None or Text is None:
        print(f"{LOG_PREFIX} 正在思考中…")
        return

    _console.print(Text("  * 正在思考中...", style="bold magenta"))
