"""Shared state definition for the InkFlow LangGraph workflow.

LangGraph passes one shared "state" object between nodes.
For a beginner-friendly first version, we use a TypedDict:

- It behaves like a normal Python dictionary at runtime.
- It also tells editors/type checkers what keys we expect.
- Each graph node can read existing keys and return updated keys.
"""

from typing import NotRequired, TypedDict


class InkFlowState(TypedDict):
    """State shared by every node in the workflow.

    Think of this as the "work order" moving through the factory:
    each node reads the current values, adds or changes a few fields,
    then passes the state to the next node.
    """

    # Original text from the user, a local note, RSS item, GitHub release, etc.
    raw_text: str

    # Text after basic cleanup and simple sensitive-word replacement.
    clean_text: NotRequired[str]

    # Draft article content. In this first version it is only placeholder text.
    draft: NotRequired[str]

    # Current review status. Later this can drive human-in-the-loop branching.
    review_status: NotRequired[str]

    # Non-fatal notes collected during the workflow.
    warnings: NotRequired[list[str]]
