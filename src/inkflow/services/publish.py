"""静态博客发布服务。

本模块只执行程序配置好的固定命令序列：
复制审阅稿 -> 构建检查 -> git add -> git commit -> git push。
LLM 不参与命令生成，避免把发布动作变成不可控的自由工具调用。
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


def copy_review_to_blog(review_path: Path, blog_repo: Path, content_dir: str) -> Path:
    """把审阅稿复制到 Astro 博客仓库的文章目录。

    目标路径必须位于 ``blog_repo / content_dir`` 之下。
    这个检查可以防止配置里出现 ``..`` 时把文件复制到仓库外。
    """

    source_path = review_path.resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"审阅稿不存在：{review_path}")

    repo_path = blog_repo.resolve()
    content_root = (repo_path / content_dir).resolve()
    destination_path = (content_root / source_path.name).resolve()

    if not _is_relative_to(destination_path, content_root):
        raise ValueError(f"发布目标路径越界：{destination_path}")

    content_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
    return destination_path


def run_publish_command(command: list[str], cwd: Path) -> dict[str, object]:
    """执行一条发布命令，并记录 stdout、stderr 和 exit_code。

    这里使用 ``shell=False`` 的列表参数执行命令。
    这样命令不会经过 shell 拼接，也就不会把配置字符串解释成任意脚本。
    """

    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as error:
        return {
            "command": command,
            "cwd": str(cwd),
            "exit_code": 127,
            "stdout": "",
            "stderr": str(error),
        }

    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def publish_reviewed_draft(
    review_path: Path,
    title: str,
    blog_repo: Path,
    content_dir: str,
    build_command: str | list[str],
    commit_message_template: str,
) -> tuple[Path, list[dict[str, object]]]:
    """复制审阅稿并按固定顺序执行发布命令。

    如果某一步命令失败，后续命令不会继续执行。
    已执行步骤的结果会完整返回给 LangGraph 节点写入状态和审计事件。
    """

    published_path = copy_review_to_blog(review_path, blog_repo, content_dir)
    repo_path = blog_repo.resolve()
    publish_log: list[dict[str, object]] = []

    command_plan = [
        _parse_publish_command(build_command),
        ["git", "add", _relative_to_repo(published_path, repo_path)],
        ["git", "commit", "-m", commit_message_template.format(title=title)],
        ["git", "push"],
    ]

    for command in command_plan:
        result = run_publish_command(command, repo_path)
        publish_log.append(result)
        if result["exit_code"] != 0:
            break

    return published_path, publish_log


def _parse_publish_command(command: str | list[str]) -> list[str]:
    """把配置里的构建命令收敛成列表形式。"""

    if isinstance(command, list):
        return [str(part) for part in command if str(part).strip()]

    command_text = str(command).strip()
    if not command_text:
        raise ValueError("publish.build_command 不能为空。")

    return shlex.split(command_text)


def _relative_to_repo(path: Path, repo_path: Path) -> str:
    """返回相对仓库根目录的路径，方便 git add 使用。"""

    resolved_path = path.resolve()
    resolved_repo = repo_path.resolve()
    if not _is_relative_to(resolved_path, resolved_repo):
        raise ValueError(f"路径不在博客仓库内：{resolved_path}")
    return resolved_path.relative_to(resolved_repo).as_posix()


def _is_relative_to(path: Path, parent: Path) -> bool:
    """兼容不同 Python 版本的 Path.is_relative_to 写法。"""

    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
