"""脱敏方案生成服务。

LangGraph 节点只关心“给我一段文本，返回敏感项列表”。
本模块负责行号补充、LLM 调用和 JSON 解析这些细节。
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Literal, cast

from inkflow.llm import (
    DEFAULT_LLM_CONFIG_PATH,
    LLMMessage,
    call_llm,
    is_llm_enabled,
    load_llm_config,
)
from inkflow.prompts import build_redaction_messages
from inkflow.state import RedactionDecision, RedactionFinding


def add_line_numbers(text: str) -> str:
    """给原文加行号，帮助 LLM 返回可追踪的敏感项位置。"""

    return "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(text.splitlines(), start=1)
    )


def generate_redaction_findings(
    text: str,
    *,
    config_path: Path = DEFAULT_LLM_CONFIG_PATH,
) -> list[RedactionFinding]:
    """生成脱敏审查 findings。

    如果 LLM 配置未启用，返回空列表，让当前学习项目仍可本地跑通。
    如果 LLM 已启用但返回内容不是合法 JSON，则返回一条 high 风险 finding，
    明确提示后续人工介入，而不是静默进入发布流程。
    """

    if not is_llm_enabled(config_path):
        return []

    config = load_llm_config(config_path)
    numbered_text = add_line_numbers(text)
    try:
        response_text = call_llm(build_redaction_messages(numbered_text), config=config)
    except Exception as error:
        return [_build_generation_error_finding(error)]

    try:
        raw_findings = _parse_json_array(response_text)
    except ValueError:
        return [_build_invalid_json_finding(response_text)]

    findings: list[RedactionFinding] = []
    for index, raw_finding in enumerate(raw_findings, start=1):
        if not isinstance(raw_finding, dict):
            return [_build_invalid_json_finding(response_text)]
        findings.append(_normalize_finding(raw_finding, index))

    return findings


def apply_redaction_with_llm(
    text: str,
    decisions: list[RedactionDecision],
    extra_instruction: str = "",
    *,
    config_path: Path = DEFAULT_LLM_CONFIG_PATH,
) -> str:
    """根据用户确认的修改方案，让 LLM 返回修改后的完整文本。

    这个函数只处理“已经被用户确认要修改”的条目。
    如果没有 decisions，就直接返回原文，避免模型进行额外发挥。
    """

    if not decisions:
        return text

    if not is_llm_enabled(config_path):
        raise RuntimeError("LLM 未启用，无法执行需要模型改写的脱敏决策。")

    config = load_llm_config(config_path)
    return call_llm(
        _build_apply_redaction_messages(text, decisions, extra_instruction),
        config=config,
    ).strip()


def build_text_diff(before: str, after: str) -> str:
    """生成 unified diff，供用户确认脱敏修改是否符合预期。"""

    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )


def _build_apply_redaction_messages(
    text: str,
    decisions: list[RedactionDecision],
    extra_instruction: str,
) -> list[LLMMessage]:
    """组装“按用户决策执行脱敏”的 LLM messages。"""

    decisions_text = json.dumps(decisions, ensure_ascii=False, indent=2)
    extra_text = extra_instruction.strip() or "无"

    return [
        {
            "role": "system",
            "content": (
                "你是 InkFlow 的内容脱敏改写助手。"
                "你只能按照用户已经确认的 decisions 修改文本，不能新增其它改写。"
                "请尽量保留原文结构、段落顺序、语气和 Markdown 格式。"
                "只返回修改后的完整正文，不要返回解释、标题、代码块或 diff。"
            ),
        },
        {
            "role": "user",
            "content": f"""请根据下方 decisions 对原文执行脱敏改写。

要求：
- 只修改 decisions 覆盖的内容。
- 对每条 decision 使用 user_instruction 作为最终处理方案。
- 保留原文其它内容不变。
- 返回完整修改后文本。

decisions:
{decisions_text}

额外重试建议:
{extra_text}

原文:
{text}
""",
        },
    ]


def _parse_json_array(response_text: str) -> list[Any]:
    """解析模型返回的 JSON 数组。

    第一版要求模型只返回 JSON；这里额外兼容它不小心包了一层说明文字的情况，
    尝试截取第一个数组片段再解析。
    """

    stripped_text = response_text.strip()
    try:
        parsed = json.loads(stripped_text)
    except json.JSONDecodeError:
        start = stripped_text.find("[")
        end = stripped_text.rfind("]")
        if start == -1 or end == -1 or end < start:
            raise ValueError("LLM 未返回 JSON 数组。") from None
        parsed = json.loads(stripped_text[start : end + 1])

    if not isinstance(parsed, list):
        raise ValueError("LLM 返回的 JSON 根节点不是数组。")

    return parsed


def _normalize_finding(raw_finding: dict[str, Any], index: int) -> RedactionFinding:
    """把模型返回的字典收敛成 RedactionFinding 需要的字段。"""

    risk = raw_finding.get("risk")
    if risk not in {"high", "medium", "low"}:
        risk = "high"
    normalized_risk = cast(Literal["high", "medium", "low"], risk)

    return {
        "id": _string_value(raw_finding.get("id"), f"S{index:03d}"),
        "risk": normalized_risk,
        "line_start": _int_value(raw_finding.get("line_start"), 1),
        "line_end": _int_value(
            raw_finding.get("line_end"),
            _int_value(raw_finding.get("line_start"), 1),
        ),
        "original_excerpt": _string_value(raw_finding.get("original_excerpt"), ""),
        "issue": _string_value(raw_finding.get("issue"), "敏感项"),
        "reason": _string_value(raw_finding.get("reason"), "模型认为该内容需要人工确认。"),
        "suggestion": _string_value(raw_finding.get("suggestion"), ""),
    }


def _build_invalid_json_finding(response_text: str) -> RedactionFinding:
    """构造一条格式错误 finding，阻止坏格式结果被当成正常审查结果。"""

    return {
        "id": "S_FORMAT_ERROR",
        "risk": "high",
        "line_start": 1,
        "line_end": 1,
        "original_excerpt": response_text[:200],
        "issue": "模型输出格式错误",
        "reason": "模型没有返回合法 JSON 数组，需要停止并由人工处理。",
        "suggestion": "请检查模型输出，必要时重新运行脱敏审查。",
    }


def _build_generation_error_finding(error: Exception) -> RedactionFinding:
    """构造一条模型调用失败 finding，让后续人工审查能看到风险。"""

    return {
        "id": "S_GENERATION_ERROR",
        "risk": "high",
        "line_start": 1,
        "line_end": 1,
        "original_excerpt": "",
        "issue": "脱敏方案生成失败",
        "reason": f"模型调用失败，当前不能确认文本是否适合公开发布：{error}",
        "suggestion": "请先修复 LLM 配置或手动完成脱敏审查。",
    }


def _string_value(value: Any, default: str) -> str:
    """把模型字段转成字符串，避免 None 或数字直接进入状态。"""

    if value is None:
        return default
    return str(value)


def _int_value(value: Any, default: int) -> int:
    """把模型字段转成整数行号，转换失败时使用默认值。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default
