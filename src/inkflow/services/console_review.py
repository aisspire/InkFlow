"""终端人工审查服务。

这个模块只负责和用户做简单的命令行交互：
逐条展示 LLM 发现的敏感项，然后收集用户对每一条的处理决定。
"""

from pathlib import Path

from inkflow.console_log import log_hidden_content, log_item, log_message, log_section
from inkflow.services.console_input import read_user_input
from inkflow.state import RedactionDecision, RedactionFinding


def review_redaction_findings(
    findings: list[RedactionFinding],
) -> tuple[str, list[RedactionDecision]]:
    """逐条展示敏感项，让用户选择停止、忽略或采用最终修改方案。

    返回值中的 status 有两种：
    - "stop"：用户选择停止流程。
    - "continue"：用户完成审查，可以进入下一步。

    decisions 只记录需要执行脱敏的条目；选择忽略的 finding 不会进入列表。
    """

    if not findings:
        log_section("Redaction Review")
        log_message("未发现需要人工处理的敏感项。")
        return "continue", []

    decisions: list[RedactionDecision] = []

    log_section("Redaction Review")
    log_message(
        "逐条确认脱敏方案：输入 s 停止，n 忽略，直接回车采用建议，或输入你的最终修改方案。"
    )

    for finding in findings:
        _print_finding(finding)
        user_input = read_user_input("你的选择：")

        if user_input.lower() == "s":
            return "stop", decisions

        if user_input.lower() == "n":
            continue

        # 空回车表示采用 LLM 的 suggestion；其它输入则作为用户最终方案。
        user_instruction = finding["suggestion"] if user_input == "" else user_input
        decisions.append(
            {
                "finding_id": finding["id"],
                "action": "apply",
                "line_start": finding["line_start"],
                "line_end": finding["line_end"],
                "issue": finding["issue"],
                "user_instruction": user_instruction,
            }
        )

    return _confirm_continue(decisions)


def _print_finding(finding: RedactionFinding) -> None:
    """把一条 finding 打印成适合人工快速判断的格式，但不直出原文片段。"""

    print()
    log_section(f"Redaction Finding {finding['id']}")
    log_item("风险", finding["risk"])
    log_item("位置", f"第 {finding['line_start']} 行到第 {finding['line_end']} 行")
    log_item("问题", finding["issue"])
    log_item("原因", finding["reason"])
    log_hidden_content("原文片段")
    log_item("建议", finding["suggestion"])


def _confirm_continue(
    decisions: list[RedactionDecision],
) -> tuple[str, list[RedactionDecision]]:
    """所有 finding 审查完后，让用户明确是否进入下一步。"""

    while True:
        user_input = read_user_input("输入 y 进入下一步，输入 s 结束流程：").lower()
        if user_input == "y":
            return "continue", decisions
        if user_input == "s":
            return "stop", decisions
        log_message("请输入 y 或 s。")


def confirm_redaction_diff(
    diff: str,
    artifact_path: Path | None = None,
) -> tuple[str, str]:
    """隐藏终端里的脱敏 diff，并让用户决定接受、重试或停止。

    当调用方传入 artifact_path 时，完整 diff 会先写入这个本地文件，
    终端只提示文件路径。

    返回值：
    - ("accept", "")：用户确认本次修改。
    - ("retry", "")：用户不接受，但没有补充说明。
    - ("retry", "补充说明")：用户给出额外修改建议。
    - ("stop", "")：用户停止流程。
    """

    log_section("Redaction Diff")
    if diff:
        if artifact_path is not None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(diff, encoding="utf-8")
        log_hidden_content("本次 diff", path=artifact_path)
    else:
        log_message("本次改写没有产生文本差异。")

    log_message("输入 y 确认修改，输入 n 重新尝试，输入 s 停止，或输入额外建议后重新修改。")
    while True:
        user_input = read_user_input("你的选择：")
        lowered_input = user_input.lower()
        if lowered_input == "y":
            return "accept", ""
        if lowered_input == "n":
            return "retry", ""
        if lowered_input == "s":
            return "stop", ""
        if user_input:
            return "retry", user_input
        log_message("请输入 y、n、s，或直接写下额外建议。")
