"""InkFlow 的状态定义入口。

外部模块优先从这里导入状态类型，避免关心状态文件的内部拆分方式。
"""

from inkflow.state.workflow import InkFlowState

__all__ = ["InkFlowState"]
