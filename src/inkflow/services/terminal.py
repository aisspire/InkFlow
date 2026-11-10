"""终端只适配人工等待事件，不持有另一套业务工作流。"""

from pathlib import Path
from uuid import uuid4

from langgraph.types import Command

from inkflow.actions import validate_action
from inkflow.console_log import log_item, log_message, log_section
from inkflow.services.console_input import read_user_input
from inkflow.services.console_review import confirm_redaction_diff
from inkflow.services.local_files import atomic_write


def terminal_action(pending: dict, state: dict) -> dict:
    kind = pending["kind"]
    if kind == 'suggestion_review':
        for suggestion in state.get('suggestions', []):
            log_item(suggestion['title'], suggestion['reason'])
        return {'action': 'stop' if read_user_input('回车结束本轮，s 停止：').lower() == 's' else 'finish_check'}
    log_section({"redaction_review": "脱敏审核", "diff_review": "差异确认",
                 "article_question": "补充信息", "final_review": "最终审阅"}[kind])
    if kind == "redaction_review":
        choices = []
        if not pending["findings"]:
            log_message("没有待处理的敏感项。模型关闭时此结果不代表已通过安全检查。")
        for item in pending["findings"]:
            log_item("问题", f"{item['id']} / {item['risk']} / 第 {item['line_start']}-{item['line_end']} 行")
            log_item("原因", item["reason"])
            log_item("建议", item["suggestion"])
            text = read_user_input("回车采用建议，n 忽略，s 停止，或填写自定义修改：")
            if text.lower() == "s":
                return {"action": "stop"}
            mode = "ignore" if text.lower() == "n" else "custom" if text else "apply"
            choices.append({"finding_id": item["id"], "action": mode, "instruction": text})
        while True:
            text = read_user_input("y 继续，s 停止：").lower()
            if text in {"y", "s"}:
                return {"action": "review", "payload": {"decisions": choices}} if text == "y" else {"action": "stop"}
    if kind == "diff_review":
        config = state.get("config", {})
        directory = Path(config.get("_config_dir", ".")) / config.get("report", {}).get("dir", "reports")
        path = directory / f"{state['run_id']}-diff-preview.diff"
        atomic_write(path, pending["diff"])
        action, feedback = confirm_redaction_diff(pending["diff"], path)
        return {"action": action, "payload": {"feedback": feedback}}
    if kind == "article_question":
        log_item("问题", pending["question"])
        answer = read_user_input("你的回答（输入 /stop 停止）：")
        return {"action": "stop"} if answer == "/stop" else {"action": "answer", "payload": {"answer": answer}}
    log_item("审阅稿", pending["review_path"])
    if pending.get("degraded"):
        log_message("当前为降级占位文章，请人工检查内容。")
    target = pending["publish_preview"]
    log_item("博客目标", target.get("path") or target.get("error"))
    if target.get("exists"):
        log_message("目标文件已存在；确认写入将覆盖 index.mdx。")
    log_message("y 接受（--no-publish 时只保存），f 仅保存，d 退回脱敏，g 重新生成，s 停止。")
    text = read_user_input("其它内容作为重新生成建议：")
    action = {"y": "finish" if pending["no_publish"] else "publish", "f": "finish", "d": "redact_again",
              "g": "regenerate", "s": "stop"}.get(text.lower(), "regenerate")
    payload = {"revision": pending["revision"], "feedback": "" if len(text) == 1 else text, "target": target}
    if action == "publish" and target.get("exists"):
        payload["overwrite"] = read_user_input("确认覆盖目标文件？输入 overwrite：") == "overwrite"
    return {"action": action, "payload": payload}


def run_terminal(graph, initial_state: dict) -> dict:
    config = {"configurable": {"thread_id": initial_state.get("run_id") or uuid4().hex}, "recursion_limit": 10000}
    value = initial_state
    while True:
        # 终端只输出节点摘要；完整输入输出仍保留在本地报告。
        for mode, event in graph.stream(value, config, stream_mode=["updates", "custom"]):
            if mode == "custom" and event["type"] == "node_started":
                log_item("执行节点", event["node"])
        snapshot = graph.get_state(config)
        pending = [entry for task in snapshot.tasks for entry in task.interrupts]
        if not pending:
            return snapshot.values
        while True:
            try:
                response = terminal_action(pending[0].value, snapshot.values)
                if pending[0].value["kind"] != "suggestion_review":
                    validate_action(pending[0].value, response["action"], response.get("payload", {}))
                break
            except ValueError as error:
                log_message(str(error))
            except (EOFError, KeyboardInterrupt):
                response = {"action": "stop"}
                break
        value = Command(resume=response)
