"""审计事件记录服务。

后续发布流程会经历 LLM 判断、用户确认、文件写入和命令执行等步骤。
这些步骤都可以把关键输入输出追加到 audit_events，方便最终生成完整流程报告。
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def build_audit_event(node: str, event: str, data: dict[str, Any]) -> dict[str, Any]:
    """生成一条审计事件。

    node 表示事件来自哪个图节点或服务步骤。
    event 表示具体发生了什么，例如 llm_findings、user_decisions。
    data 保存该事件的详细数据，第一版先完整记录，方便学习和复盘。
    """

    return {
        "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        ),
        "node": node,
        "event": event,
        "data": data,
    }


def append_audit_event(
    events: list[dict[str, Any]] | None,
    node: str,
    event: str,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """在当前事件列表后追加一条事件。

    LangGraph 节点通常返回“要更新的状态片段”。
    这里复制旧列表再追加，避免直接修改传入列表导致状态变化不够清晰。
    """

    next_events = list(events or [])
    next_events.append(build_audit_event(node, event, data))
    return next_events
