"""用于尝试第一版 InkFlow 图的命令行入口。"""

import argparse
import tomllib
from pathlib import Path
from typing import Any

from inkflow.graphs import build_graph
from inkflow.state import InkFlowState


DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_EXAMPLE_TEXT = "LangGraph 很适合多阶段内容处理。这里有一个 password 示例。"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    argparse 是 Python 标准库里的命令行参数解析工具。
    这里先只支持两个参数，保持入口简单：
    - --config：指定配置文件路径。
    - --input：直接指定要读取的输入文件路径。
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
    return parser.parse_args()


def load_config(config_path: Path) -> dict[str, Any]:
    """读取 TOML 配置文件。

    Python 3.11 自带 tomllib，可以读取 TOML 格式。
    如果配置文件不存在，先返回空配置，让 main 继续使用示例文本。
    """

    if not config_path.exists():
        return {}

    with config_path.open("rb") as config_file:
        return tomllib.load(config_file)


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

    args = parse_args()
    config = load_config(Path(args.config))
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
        "warnings": [],
    }

    # invoke() 会让图从 START 跑到 END，并返回最终状态。
    final_state = app.invoke(initial_state)

    print("=== Review Status ===")
    print(final_state["review_status"])
    print()

    print("=== Draft ===")
    print(final_state["draft"])
    print()

    print("=== Warnings ===")
    if final_state["warnings"]:
        for warning in final_state["warnings"]:
            print(f"- {warning}")
    else:
        print("无")


if __name__ == "__main__":
    main()
