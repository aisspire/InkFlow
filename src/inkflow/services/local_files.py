"""本地文件写入的公共边界：同目录临时文件 + 原子替换。"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path

from inkflow.services.file_permissions import restrict_access


def atomic_write(path: Path, content: str, *, private: bool = False, exclusive: bool = False) -> None:
    from inkflow.web.durable import write_effect
    return write_effect(path, content, lambda: _atomic_write(path, content, private=private, exclusive=exclusive))


def _atomic_write(path: Path, content: str, *, private: bool = False, exclusive: bool = False) -> None:
    """先写完整内容再替换目标，避免异常把已有文章截成半份。

    私有文件在写入正文前设为 POSIX 0600 / Windows 当前用户 DACL。
    普通 POSIX 文件保留目标模式，新文件按 0666 与当前 umask 创建。
    临时文件与目标在同一目录，保证 os.replace 不跨文件系统。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if private or os.name == "nt":
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
    else:
        # os.open 让内核应用 umask，避免读取/修改进程级 umask 的线程竞争。
        for _ in range(100):
            temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}"
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError("无法创建唯一临时文件。")
    attempted_replace = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            if private:
                restrict_access(temporary)
            elif os.name != "nt" and path.exists():
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # 原子发布新文件；另一个进程抢先创建时失败，绝不覆盖。
            attempted_replace = True
            os.link(temporary, path)
        else:
            attempted_replace = True
            os.replace(temporary, path)
    except Exception as error:
        if not attempted_replace:
            error.effect_not_written = True
        raise
    finally:
        temporary.unlink(missing_ok=True)
