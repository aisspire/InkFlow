"""发布流程报告服务。

报告目录保留在 InkFlow 本地项目内，允许记录完整敏感内容。
这类文件用于复盘流程，不应该复制到静态博客仓库或公开发布。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from inkflow.state import InkFlowState


def build_run_id() -> str:
    """生成本次运行的报告 ID。"""

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")


def write_audit_jsonl(
    events: list[dict[str, Any]],
    report_dir: Path,
    run_id: str,
) -> Path:
    """按时间顺序写入完整审计事件，允许包含敏感内容。"""

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{run_id}.jsonl"
    with report_path.open("w", encoding="utf-8") as report_file:
        for event in events:
            report_file.write(json.dumps(event, ensure_ascii=False, default=str))
            report_file.write("\n")
    return report_path


def write_markdown_report(
    state: InkFlowState,
    report_dir: Path,
    run_id: str,
) -> Path:
    """写人类可读的流程报告。

    这份 Markdown 报告会把关键状态串起来，方便用户不用翻 JSONL
    也能看到脱敏、生成、审阅和发布的结果。
    """

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{run_id}.md"
    report_path.write_text(_build_report_text(state, run_id), encoding="utf-8")
    return report_path


def _build_report_text(state: InkFlowState, run_id: str) -> str:
    """把工作流状态整理成 Markdown 文本。"""

    final_text = state.get("redacted_text") or state.get("clean_text") or state["raw_text"]

    sections = [
        f"# InkFlow Publishing Report\n\n- run_id: `{run_id}`\n- review_status: `{state.get('review_status', '')}`\n",
        _text_section("输入文件路径", state.get("source_path") or "未指定"),
        _text_section("所有脱敏方案", _json_text(state.get("redaction_findings", []))),
        _text_section("用户脱敏决策", _json_text(state.get("redaction_decisions", []))),
        _text_section("脱敏 diff", state.get("redaction_diff") or "无"),
        _text_section("最终进入生成节点的文本", final_text),
        _text_section("生成出来的 JSON", _json_text(state.get("article_data", {}))),
        _text_section("拼装后的 Markdown", state.get("final_document") or "无"),
        _text_section(
            "审阅记录",
            _json_text(
                {
                    "review_path": state.get("review_path", ""),
                    "review_action": state.get("review_action", ""),
                    "approved": state.get("approved", False),
                    "article_feedback": state.get("article_feedback", ""),
                }
            ),
        ),
        _text_section("发布目标路径", state.get("publish_path") or "无"),
        _text_section("命令返回结果", _json_text(state.get("publish_log", []))),
        _text_section("流程提示", _json_text(state.get("warnings", []))),
    ]
    return "\n".join(sections).rstrip() + "\n"


def _text_section(title: str, content: str) -> str:
    """生成一个 Markdown 小节。"""

    return f"## {title}\n\n{content}\n"


def _json_text(value: object) -> str:
    """把结构化数据转成 Markdown 代码块。"""

    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n```"
