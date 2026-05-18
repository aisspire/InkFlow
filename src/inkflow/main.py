"""Command line entrypoint for trying the first InkFlow graph."""

from inkflow.graph import build_graph
from inkflow.state import InkFlowState


def main() -> None:
    """Run the minimal workflow once with example input."""

    app = build_graph()

    # This is the initial state sent into the graph.
    # Later, raw_text can come from a Markdown file, RSS item, or GitHub release.
    initial_state: InkFlowState = {
        "raw_text": "LangGraph 很适合多阶段内容处理。这里有一个 password 示例。",
        "warnings": [],
    }

    # invoke() runs the graph from START to END and returns the final state.
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
