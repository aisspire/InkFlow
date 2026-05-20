"""InkFlow 的图定义入口。

目前只导出最小内容工作流。未来如果出现本地笔记、GitHub Release、
新闻等多条流程，可以继续在这个包里增加新的图构建函数。
"""

from inkflow.graphs.minimal import build_graph

__all__ = ["build_graph"]
