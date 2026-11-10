"""InkFlow 的图定义入口。

保留既有 build_graph 导出以兼容直接调用。
Web 与 CLI 通过 inkflow.workflows 注册表选择实际的图构建入口。
"""

from inkflow.graphs.minimal import build_graph

__all__ = ["build_graph"]
