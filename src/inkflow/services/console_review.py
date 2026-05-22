"""终端人工审查服务。

这个模块只负责和用户做简单的命令行交互：
逐条展示 LLM 发现的敏感项，然后收集用户对每一条的处理决定。
"""

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
        print("=== Redaction Review ===")
        print("未发现需要人工处理的敏感项。")
        return "continue", []

    decisions: list[RedactionDecision] = []

    print("=== Redaction Review ===")
    print("逐条确认脱敏方案：输入 s 停止，n 忽略，直接回车采用建议，或输入你的最终修改方案。")

    for finding in findings:
        _print_finding(finding)
        user_input = _read_user_input("你的选择：")

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
    """把一条 finding 打印成适合人工快速判断的格式。"""

    print()
    print(f"[{finding['id']}] 风险：{finding['risk']}")
    print(f"位置：第 {finding['line_start']} 行到第 {finding['line_end']} 行")
    print(f"问题：{finding['issue']}")
    print(f"原因：{finding['reason']}")
    print(f"原文：{finding['original_excerpt']}")
    print(f"建议：{finding['suggestion']}")


def _confirm_continue(
    decisions: list[RedactionDecision],
) -> tuple[str, list[RedactionDecision]]:
    """所有 finding 审查完后，让用户明确是否进入下一步。"""

    while True:
        user_input = _read_user_input("输入 y 进入下一步，输入 s 结束流程：").lower()
        if user_input == "y":
            return "continue", decisions
        if user_input == "s":
            return "stop", decisions
        print("请输入 y 或 s。")


def _read_user_input(prompt: str) -> str:
    """读取终端输入，并兼容 Windows PowerShell 管道开头可能出现的 BOM。

    手动键盘输入通常不会带 BOM；但在本项目常用的 PowerShell smoke check 中，
    把字符串管道给 Python 时，第一行可能会带上 ``\ufeff``。
    这里统一清理它，避免把 ``s`` 误判成自定义修改方案。
    """

    return input(prompt).lstrip("\ufeff").strip()
