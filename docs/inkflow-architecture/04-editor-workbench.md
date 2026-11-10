# 文章工作副本、版本与标注

当前业务规则见[统一工作台](../workbench-and-workflows.md#工作台规则)。

EditorService（`web/editor.py`）负责授权工作区文件、UTF-8 原文、当前工作副本、revision、历史快照、磁盘指纹和原子写入。身份由工作区绝对路径与规范化相对路径组成。打开读取磁盘；已有脏草稿优先保留。外部文件变化且工作副本没有修改时可重载；保存前仍必须核对指纹。frontmatter、MDX、BOM、CRLF 和混合换行以原始字符串保留，不从 Markdown 渲染结果重建文件。

`editor_docs` 保存当前工作副本，`editor_versions` 保存版本与操作。恢复版本、采用建议和已确认脱敏候选均产生新版本；候选替换在同一 SQLite 事务保存前后稿，恢复前也保存当前稿。只有 save 写回磁盘。历史查询最多返回 200 版，数据库不据此删除旧版。

全文检查属于 `workflows/full_check.py` 的正式图。审核事实在运行 annotations；EditorService 的旧 suggestions/check 字段只是兼容残留或受控操作投影，新功能不据此管理检查生命周期。`RunManager.annotations()` 将原始检查范围映射到当前持久化稿；`sync_document()` 持久记录移动和待复查事实。局部采用通过当前 pending、文章身份、revision、状态和实际原文逐字检查后修改工作副本；历史动作被拒绝。

后端以 UTF-16 code unit 与 CodeMirror 一致，拒绝代理对内偏移。无法可靠定位、编辑命中与存在重复片段映射歧义时不采用或高亮。全文结构建议允许空 original。脱敏检测保留 checked_text，行号只用于该快照内片段定位，不直接作为当前正文坐标；每轮复查基于已确认稿。

`SourceEditor.tsx` 按文章缓存 EditorState、编辑权限 compartment 和回调载体，切换后恢复选择、滚动与完整撤销/重做。外部采用、候选确认和恢复通过 isolateHistory 作为独立撤销步骤；预览只读，MDX/HTML 不执行。会话缓存不跨刷新，持久化草稿与版本独立于编辑撤销栈、运行 checkpoint。

已确认脱敏稿通过 RunManager 的 diff accept 自动安装，不再要求额外载入。其它文章生成稿通过 WorkflowDocumentBridge 显式对比载入；来源、候选 ID、revision 和冲突确认在后端核验，历史不能直接载入。主编辑稿可提交回 final_review，最终输出核对已提交版本。

以上三个差异入口使用 `DocumentDiff` 比较固定的前后全文，显示红色删除、绿色新增与行内标记，默认折叠未改内容。脱敏基线取本轮 `checked_text`，兼容旧记录时依次回退到 `redacted_text`、`clean_text`、`raw_text`；冲突和载入各取自己的当前稿快照。前端不解析或展示后端原始 unified diff，因此旧预览截断不影响可视化全文对比。匹配预算和每次 100 行的展开只控制显示开销，不更改接受的候选文本。详见 [WF-R08](../workbench-and-workflows.md#工作流定义与运行)。
