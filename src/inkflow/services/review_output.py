"""审阅稿输出和终端流转服务。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from inkflow.console_log import log_item, log_message, log_section
from inkflow.services.console_input import read_user_input


def write_review_file(document: str, title: str, review_dir: Path) -> Path:
    """把待发布文章写入本地 reviews 目录。

    这个目录仍在 InkFlow 项目本地，用来给用户人工检查。
    真正复制到博客仓库的动作会放到后续发布节点里，避免审阅未完成就外发。
    """

    review_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    base_path = review_dir / f"{date_prefix}-{_safe_slug(title)}.md"
    review_path = _next_available_path(base_path)
    review_path.write_text(document, encoding="utf-8")
    return review_path


def review_generated_draft(review_path: Path) -> tuple[str, str]:
    """展示审阅稿路径，并收集用户下一步选择。

    返回值：
    - accepted：接受审阅稿，后续进入发布阶段。
    - regenerate：回到文章生成阶段。
    - redact_again：回到脱敏审查阶段。
    - stopped：停止流程。
    第二个返回值用于保存“其它内容”这种重新生成建议。
    """

    log_section("Review Draft")
    log_item("审阅稿", review_path)
    log_message("y：接受并进入发布")
    log_message("d：回到脱敏阶段")
    log_message("g：回到生成阶段")
    log_message("s：停止流程")
    log_message("其它内容：作为修改建议回到生成阶段")

    user_input = read_user_input("你的选择：")
    lowered_input = user_input.lower()
    if lowered_input == "y":
        return "accepted", ""
    if lowered_input == "d":
        return "redact_again", ""
    if lowered_input == "g":
        return "regenerate", ""
    if lowered_input == "s":
        return "stopped", ""
    return "regenerate", user_input


def _safe_slug(title: str) -> str:
    """把标题转换成适合作为文件名的短 slug。"""

    slug = title.strip().lower()
    slug = re.sub(r'[\\/:*?"<>|]+', "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = slug.strip(".-")
    return slug[:60] or "article"


def _next_available_path(path: Path) -> Path:
    """避免覆盖已有审阅稿；重名时追加数字后缀。"""

    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"无法为审阅稿找到可用文件名：{path}")
