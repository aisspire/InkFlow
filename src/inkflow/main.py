"""用于尝试第一版 InkFlow 图的命令行入口。"""

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from inkflow.console_log import log_hidden_content, log_item, log_message, log_section
from inkflow.graphs import build_graph
from inkflow.state import InkFlowState


DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_EXAMPLE_TEXT = "LangGraph 很适合多阶段内容处理。这里有一个 password 示例。"


def configure_utf8_stdio() -> None:
    """让 Windows 终端也能稳定打印 UTF-8 内容。

    工作流现在不会把原文、diff 和 Markdown 原样打到终端，
    但日志里仍然会出现中文路径、状态和错误提示。
    如果 PowerShell 当前输出编码是 GBK，遇到少见符号时可能抛出 UnicodeEncodeError。
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    argparse 是 Python 标准库里的命令行参数解析工具。
    这里先只支持少量参数，保持入口简单：
    - --config：指定配置文件路径。
    - --input：直接指定要读取的输入文件路径。
    - --no-publish：只生成审阅稿和报告，不执行博客发布命令。
    """

    parser = argparse.ArgumentParser(description="运行 InkFlow 最小工作流。")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="配置文件路径，默认读取当前目录下的 config.toml。",
    )
    parser.add_argument(
        "--input",
        help="输入文件路径；如果传入它，会覆盖配置文件里的 input_path。",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="只生成审阅稿和报告；即使审阅时选择发布，也不会执行复制、构建、git 提交或推送。",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> dict[str, Any]:
    """读取 TOML 配置文件。

    Python 3.11 自带 tomllib，可以读取 TOML 格式。
    如果配置文件不存在，先返回空配置，让 main 继续使用示例文本。
    """

    if not config_path.exists():
        return {}

    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)

    # 图节点需要按配置文件所在目录解析 reviews 等相对路径。
    # 这里把目录放进状态，避免节点自己重新读配置文件。
    config["_config_dir"] = str(config_path.parent)
    return config


def resolve_input_path(args: argparse.Namespace, config: dict[str, Any]) -> Path | None:
    """按“命令行优先，配置文件其次”的顺序决定输入文件路径。"""

    if args.input:
        # 命令行参数通常以运行命令时所在目录为参照。
        return Path(args.input)

    input_path = config.get("input_path")
    if not input_path:
        return None

    # 配置文件里的相对路径按配置文件所在目录解析，方便移动整套配置。
    config_path = Path(args.config)
    return config_path.parent / Path(input_path)


def read_input_text(input_path: Path | None) -> str:
    """读取输入文件内容；没有路径时保留第一版示例输入。"""

    if input_path is None:
        return DEFAULT_EXAMPLE_TEXT

    return input_path.read_text(encoding="utf-8")


def main() -> None:
    """读取输入文件并运行一次最小工作流。"""

    configure_utf8_stdio()

    args = parse_args()
    config = load_config(Path(args.config))
    if args.no_publish:
        # 命令行开关优先级最高，用配置状态告诉图路由跳过发布节点。
        publish_config = config.setdefault("publish", {})
        if not isinstance(publish_config, dict):
            publish_config = {}
            config["publish"] = publish_config
        publish_config["no_publish"] = True
    input_path = resolve_input_path(args, config)
    raw_text = read_input_text(input_path)

    app = build_graph()

    # 这是传入图的初始状态。
    # 后续 raw_text 可以来自 Markdown 文件、RSS 条目或 GitHub Release。
    # 旧版写死示例保留在这里作为对照：
    # initial_state: InkFlowState = {
    #     "raw_text": "LangGraph 很适合多阶段内容处理。这里有一个 password 示例。",
    #     "warnings": [],
    # }
    initial_state: InkFlowState = {
        "raw_text": raw_text,
        "source_path": str(input_path) if input_path is not None else "",
        "config": config,
        "warnings": [],
    }

    # invoke() 会让图从 START 跑到 END，并返回最终状态。
    final_state = app.invoke(initial_state)

    report_path = final_state.get("report_path")

    log_section("Review Status")
    log_item("状态", final_state["review_status"])
    print()

    log_section("Final Document")
    if "final_document" in final_state:
        log_hidden_content(
            "最终文档",
            path=final_state.get("review_path") or report_path,
        )
    elif "draft" in final_state:
        log_hidden_content("草稿", path=report_path)
    else:
        log_message("流程已在生成文章前停止，未生成文档。")
    print()

    log_section("Review Path")
    if "review_path" in final_state:
        log_item("审阅稿", final_state["review_path"])
    else:
        log_message("未生成审阅文件。")
    print()

    log_section("Publish Path")
    if "publish_path" in final_state and final_state["publish_path"]:
        log_item("发布文件", final_state["publish_path"])
    else:
        log_message("未发布到博客仓库。")
    print()

    log_section("Publish Log")
    publish_log = final_state.get("publish_log", [])
    if publish_log:
        for index, item in enumerate(publish_log, start=1):
            exit_code = item.get("exit_code")
            log_item(
                f"命令 {index}",
                f"exit_code={exit_code}，详情请查看本地报告：{report_path}",
            )
    else:
        log_message("无")
    print()

    log_section("Report Path")
    if "report_path" in final_state:
        log_item("Markdown 报告", final_state["report_path"])
    else:
        log_message("未生成 Markdown 报告。")
    if "report_jsonl_path" in final_state:
        log_item("JSONL 审计日志", final_state["report_jsonl_path"])
    else:
        log_message("未生成 JSONL 审计日志。")
    print()

    log_section("Warnings")
    warnings = final_state.get("warnings", [])
    if warnings:
        for warning in warnings:
            log_message(warning)
    else:
        log_message("无")


if __name__ == "__main__":
    main()
