"""浏览器和终端共用的人工操作校验，过期 ID 由运行管理器校验。"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from inkflow.services.packaging import package_astro_markdown, parse_generated_document


def validate_article(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("请提交完整文章。")
    result = {}
    for key in ("title", "slug", "description", "date", "body"):
        text = value.get(key)
        if not isinstance(text, str) or (key != "description" and not text.strip()):
            raise ValueError(f"文章字段 {key} 必须为有效文本。")
        if len(text) > (1000000 if key == "body" else 2000):
            raise ValueError(f"文章字段 {key} 过长。")
        result[key] = text.strip()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", result["slug"]) or len(result["slug"]) > 80:
        raise ValueError("slug 仅允许小写英文、数字和单个连字符，最长 80 字符。")
    if re.fullmatch(r"con|prn|aux|nul|com[0-9]|lpt[0-9]", result["slug"]):
        raise ValueError("slug 不能使用 Windows 保留的设备名。")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", result["date"]):
            raise ValueError("日期格式错误。")
        date.fromisoformat(result["date"])
    except ValueError:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式。") from None
    for key in ("tags", "authors"):
        items = value.get(key)
        if (not isinstance(items, list) or len(items) > 100
                or any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in items)):
            raise ValueError(f"{key} 必须是有效的文本列表。")
        result[key] = [item.strip() for item in items]
    if not result["authors"]:
        raise ValueError("请至少填写一位作者。")
    if not isinstance(value.get("draft"), bool):
        raise ValueError("草稿标记必须为布尔值。")
    result["draft"] = value["draft"]
    return result


def validate_action(pending: dict, action: str, payload: dict) -> dict:
    """只接收当前步骤允许的操作，返回规范化的恢复值。"""

    if action == "stop":
        return {"action": "stop"}
    kind = pending["kind"]
    allowed = {"redaction_review": {"review"}, "diff_review": {"accept", "retry"},
               "article_question": {"answer"},
               "final_review": {"save", "finish", "publish", "regenerate", "redact_again"}}
    if action not in allowed.get(kind, set()):
        raise ValueError("当前步骤不支持此操作。")
    result: dict = {"action": action}
    if action == "review":
        choices = payload.get("decisions")
        findings = pending.get("findings", [])
        if not isinstance(choices, list) or len(choices) != len(findings):
            raise ValueError("请逐项完成全部脱敏审核。")
        by_id = {item.get("finding_id"): item for item in choices
                 if isinstance(item, dict) and isinstance(item.get("finding_id"), str)}
        if len(by_id) != len(findings):
            raise ValueError("脱敏审核条目重复或格式错误。")
        decisions = []
        for finding in findings:
            choice = by_id.get(finding["id"], {})
            mode = choice.get("action")
            if mode not in {"apply", "ignore", "custom"}:
                raise ValueError("请为每个脱敏项选择采用、忽略或自定义。")
            instruction = finding["suggestion"] if mode == "apply" else choice.get("instruction", "")
            if mode != "ignore" and (not isinstance(instruction, str) or not instruction.strip()):
                raise ValueError("修改方案不能为空。")
            if not isinstance(instruction, str) or len(instruction) > 10000:
                raise ValueError("修改方案过长或格式不正确。")
            decisions.append({"finding_id": finding["id"], "action": "ignore" if mode == "ignore" else "apply",
                              "line_start": finding["line_start"], "line_end": finding["line_end"],
                              "issue": finding["issue"], "user_instruction": instruction})
        result["decisions"] = decisions
    if action in {"retry", "regenerate", "redact_again", "answer"}:
        field = "answer" if action == "answer" else "feedback"
        value = payload.get(field, "")
        if not isinstance(value, str) or len(value) > 20000 or (action == "answer" and not value.strip()):
            raise ValueError("请填写有效文本（最多 20000 字符）。")
        result[field] = value.strip()
    if kind == "final_review":
        if payload.get("revision") != pending["revision"]:
            raise ValueError("文章版本已变化，请刷新后重新确认。")
        if action == "save":
            article = parse_generated_document(payload['document'], pending['article']) if 'document' in payload else payload.get('article')
            result["article"] = validate_article(article)
        if action in {"finish", "publish"} and 'document' in payload:
            article = validate_article(parse_generated_document(payload['document'], pending['article']))
            if package_astro_markdown(article) != pending['document']:
                raise ValueError('主编辑稿尚未提交到工作流，请先点击“提交编辑稿到工作流”再确认输出。')
        if action == "publish":
            preview = pending.get("publish_preview", {})
            if pending.get("no_publish") or not preview.get("path") or preview.get("error"):
                raise ValueError("当前任务无法写入博客，请检查目标设置或仅保存审阅稿。")
            if payload.get("target") != preview:
                raise ValueError("请先核对博客目标路径。")
            if preview["exists"] and payload.get("overwrite") is not True:
                raise ValueError("目标文件已存在，请明确确认覆盖。")
    return result
