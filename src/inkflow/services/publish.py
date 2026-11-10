"""博客输出只写 `<slug>/index.mdx`；不构建、不执行 Git、不触碰资源文件。"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path, PureWindowsPath

from inkflow.services.local_files import atomic_write

_OUTPUT_LOCK = threading.RLock()


def validate_content_dir(content_dir: str) -> None:
    path = Path(content_dir)
    if (not content_dir.strip() or path.is_absolute() or PureWindowsPath(content_dir).drive
            or ".." in path.parts or ".." in PureWindowsPath(content_dir).parts):
        raise ValueError("博客内容目录必须是仓库内的相对路径，不能包含 ..。")


def resolve_blog_destination(blog_repo: Path, content_dir: str, slug: str) -> Path:
    repo = blog_repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"博客仓库目录不存在：{repo}")
    validate_content_dir(content_dir)
    root = (repo / content_dir).resolve()
    if not root.is_relative_to(repo):
        raise ValueError("博客内容目录不能越过仓库根目录。")
    destination = root / _safe_slug(slug) / "index.mdx"
    # 不跟随 index.mdx 的符号链接，也不允许 slug 目录指向内容目录之外。
    if destination.is_symlink() or not destination.resolve().is_relative_to(root):
        raise ValueError("博客文章目标路径越界或目标文件为符号链接。")
    if destination.exists() and not destination.is_file():
        raise ValueError("博客目标已存在且不是普通文件。")
    return destination


def preview_blog_target(blog_repo: Path, content_dir: str, slug: str) -> dict:
    destination = resolve_blog_destination(blog_repo, content_dir, slug)
    exists = destination.exists()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest() if exists else None
    return {"path": str(destination), "exists": exists, "fingerprint": digest}


def write_final_blog_article(
    final_document: str,
    title: str,
    blog_repo: Path,
    content_dir: str,
    *,
    slug_source: str | None = None,
    expected: dict | None = None,
) -> Path:
    """核对目标仍与人工看到的一致，再原子写入已保存的最终文档。

    指纹防止等待审核期间的普通外部修改被悄悄覆盖；这是本地单用户应用，
    不承诺抵御另一个进程在检查与替换之间恶意竞争修改目录。
    """

    # 不同文章现在可以并行运行；本进程的输出核验与替换必须作为一个临界区。
    with _OUTPUT_LOCK:
        current = preview_blog_target(blog_repo, content_dir, slug_source or title)
        if expected is not None and current != expected:
            raise ValueError("目标文件已在确认后变化，未覆盖。请重新运行并检查最新目标。")
        destination = Path(current["path"])
        atomic_write(destination, final_document)
        return destination


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")[:80] or "article"
    # Windows 设备名即使带扩展名也不可用，跨平台统一规避。
    if re.fullmatch(r"con|prn|aux|nul|com[0-9]|lpt[0-9]", slug):
        slug = f"article-{slug}"
    return slug
