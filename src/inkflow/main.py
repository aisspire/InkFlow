"""用于尝试第一版 InkFlow 图的命令行入口。"""

from inkflow.graph import build_graph
from inkflow.state import InkFlowState


def main() -> None:
    """使用示例输入运行一次最小工作流。"""

    app = build_graph()

    # 这是传入图的初始状态。
    # 后续 raw_text 可以来自 Markdown 文件、RSS 条目或 GitHub Release。
    initial_state: InkFlowState = {
        "raw_text": "LangGraph 很适合多阶段内容处理。这里有一个 password 示例。",
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
