"""终端输入辅助函数。"""

from __future__ import annotations

import sys


def read_user_input(prompt: str, *, clear_pending: bool = True) -> str:
    """读取用户输入，并在真实终端里清掉提前敲入的内容。

    如果用户在 LLM 生成或文件写入期间提前按了回车，终端可能会把这些按键
    缓存在 stdin 里，导致后续 ``input()`` 直接读到空行并跳过确认步骤。
    管道输入不是人工误触，不能清空，否则 smoke check 里的预置输入会失效。
    """

    if clear_pending:
        clear_pending_stdin()
    return input(prompt).lstrip("\ufeff").strip()


def clear_pending_stdin() -> None:
    """清空当前终端已经缓冲、但还没有被 input() 消费的按键。"""

    if not sys.stdin.isatty():
        return

    try:
        import msvcrt
    except ImportError:
        _clear_posix_stdin()
        return

    while msvcrt.kbhit():
        msvcrt.getwch()


def _clear_posix_stdin() -> None:
    """在类 Unix 终端里清理已就绪的 stdin 行。"""

    try:
        import select
    except ImportError:
        return

    while select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.readline()
