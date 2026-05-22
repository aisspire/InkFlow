"""文章结构化生成服务。

LangGraph 节点只需要传入脱敏后的文本，本模块负责：
- 调用 LLM 生成文章 JSON。
- 在模型需要补充信息时，最多向用户追问 3 次。
- 在 LLM 未启用时返回本地占位文章，让学习流程可以离线跑通。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from inkflow.llm import (
    DEFAULT_LLM_CONFIG_PATH,
    call_llm,
    is_llm_enabled,
    load_llm_config,
)
from inkflow.prompts import build_article_messages
from inkflow.state import ArticleData

MAX_ARTICLE_QUESTIONS = 3


def generate_article_data(
    text: str,
    *,
    extra_instruction: str = "",
    config_path: Path = DEFAULT_LLM_CONFIG_PATH,
) -> tuple[ArticleData, list[dict[str, str]]]:
    """生成 Astro 文章所需的结构化数据。

    返回值包含两部分：
    - article：最终文章 JSON。
    - qa_history：模型追问和用户回答，节点会把它写入审计事件。
    """

    today = _today_string()
    if not is_llm_enabled(config_path):
        return build_placeholder_article(text, today=today), []

    config = load_llm_config(config_path)
    qa_history: list[dict[str, str]] = []

    for _ in range(MAX_ARTICLE_QUESTIONS + 1):
        response_text = call_llm(
            build_article_messages(text, today, qa_history, extra_instruction),
            config=config,
        )
        response_data = _parse_json_object(response_text)

        if response_data.get("needs_user_input") is True:
            if len(qa_history) >= MAX_ARTICLE_QUESTIONS:
                raise RuntimeError("模型连续追问超过 3 次，已停止文章生成以避免无限循环。")

            question = _string_value(response_data.get("question"), "请补充这篇文章的写作方向：")
            print("=== Article Question ===")
            print(question)
            answer = _read_user_input("你的回答：")
            qa_history.append({"question": question, "answer": answer})
            continue

        article = response_data.get("article")
        if not isinstance(article, dict):
            raise ValueError("模型返回的 JSON 缺少 article 对象。")

        return _normalize_article(article, today), qa_history

    raise RuntimeError("文章生成循环异常结束。")


def build_placeholder_article(text: str, *, today: str | None = None) -> ArticleData:
    """在 LLM 未启用或失败时生成本地占位文章。

    占位文章不是最终质量稿，但它能帮助我们验证后续 Markdown 打包、
    文件写入和图路由是否正常。
    """

    active_today = today or _today_string()
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    title = first_line[:40] or "InkFlow Draft"

    return {
        "title": title,
        "description": "由 InkFlow 本地占位逻辑生成的文章描述。",
        "date": active_today,
        "tags": ["InkFlow"],
        "draft": False,
        "body": text.strip() or "（空输入）",
    }


def _parse_json_object(response_text: str) -> dict[str, Any]:
    """解析模型返回的 JSON 对象，并兼容前后夹杂少量文本的情况。"""

    stripped_text = response_text.strip()
    try:
        parsed = json.loads(stripped_text)
    except json.JSONDecodeError:
        start = stripped_text.find("{")
        end = stripped_text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("LLM 未返回 JSON 对象。") from None
        parsed = json.loads(stripped_text[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("LLM 返回的 JSON 根节点不是对象。")

    return parsed


def _normalize_article(raw_article: dict[str, Any], today: str) -> ArticleData:
    """把模型输出收敛成 ArticleData，避免缺字段影响后续节点。"""

    tags = raw_article.get("tags")
    normalized_tags = [str(tag) for tag in tags] if isinstance(tags, list) else []

    return {
        "title": _string_value(raw_article.get("title"), "InkFlow Draft"),
        "description": _string_value(raw_article.get("description"), ""),
        "date": _string_value(raw_article.get("date"), today),
        "tags": normalized_tags,
        "draft": _bool_value(raw_article.get("draft"), False),
        "body": _string_value(raw_article.get("body"), ""),
    }


def _today_string() -> str:
    """返回上海时区的今天日期，保证本地流程日期一致。"""

    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _string_value(value: Any, default: str) -> str:
    """把模型字段转成字符串，空值使用默认值。"""

    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _bool_value(value: Any, default: bool) -> bool:
    """把模型字段收敛为布尔值。"""

    if isinstance(value, bool):
        return value
    return default


def _read_user_input(prompt: str) -> str:
    """读取终端输入，并清理 PowerShell 管道输入可能带来的 BOM。"""

    return input(prompt).lstrip("\ufeff").strip()
