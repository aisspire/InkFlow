# InkFlow 后续学习计划

- [ ] 读取本地 Markdown：让 `main.py` 不再写死示例文本，改为读取本地 `.md` 文件。
- [ ] 拆出预处理模块：把清洗、脱敏逻辑从 `graph.py` 挪到独立模块。
- [ ] 新增大纲节点：增加 `outline_node`，流程变为 `preprocess -> outline -> draft -> review`。
- [ ] 扩展 state 字段：增加 `outline`，理解什么时候需要给 state 增加字段。
- [ ] 改造草稿节点：让 `draft_node` 根据 `outline` 生成草稿。
- [ ] 增加审核报告：增加 `review_report`，记录空内容、敏感词、人工审核状态等信息。
- [ ] 学习条件边：根据审核结果决定继续、返回修改或结束流程。
- [ ] 接入真实 LLM：先只接入一个节点，例如 `outline_node` 或 `draft_node`。
- [ ] 引入 RAG 检索：增加历史文章检索节点，例如 `retrieve_history_node`。
- [ ] 增加人工审核入口：先使用终端确认，再考虑 Web UI。
