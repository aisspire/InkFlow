# 文章工作副本、版本与标注

当前业务规则见[统一工作台](../workbench-and-workflows.md#工作台规则)。文章侧栏仅从打开历史构造真实路径树，未命名稿单列；默认保存目录仅预填首次保存位置，不扫描目录或迁移文章。显式移除入口保留草稿和历史，最近文章可重新加入。

DocumentService（`web/documents.py`）负责精确文件登记与稳定 document_id，WorkspaceService 管理目录范围和发现；WorkspaceEditor 将旧路径接口在原校验内转为 ID。EditorService（`web/editor.py`）继续提供 UTF-8 原文、工作副本、revision、历史、候选与原子保存。身份由规范化文件位置唯一映射，与当前工作区无关；未命名文档先有 ID。打开读取磁盘；已有脏草稿优先保留。外部文件变化且工作副本没有修改时可重载；保存前仍必须核对指纹。frontmatter、MDX、BOM、CRLF 和混合换行以原始字符串保留，不从 Markdown 渲染结果重建文件。

`document_copies` 保存当前工作副本，`document_versions` 保存版本与操作；旧 editor 表保留迁移前快照，document_aliases 记录旧键映射。同文件不同旧草稿保留 migration_conflict，界面提示核对，冲突历史不受 200 版窗口限制。恢复版本、采用建议和已确认脱敏候选均产生新版本；候选替换在同一 SQLite 事务保存前后稿，恢复前也保存当前稿。draft_pending 独立记录未写回语义，恢复或采用候选即使文本与磁盘相同仍显示为草稿，显式保存清除。只有 save／首次保存／另存为写入文件。首次保存保持 ID，另存为创建新文件和 ID 并记录来源；独占原子发布拒绝同名覆盖。失联稿可继续编辑或另存为，不猜测原位置。历史查询最多返回 200 版，数据库不据此删除旧版。

失联文章可显式重新关联移动后的文件：原位置需已不存在，目标未登记且指纹匹配原磁盘版本，保留 ID、草稿、历史与运行，不移动文件或合并身份。关联不同内容需独立打开核对或另存为。

全文检查属于 `workflows/full_check.py` 的正式图。审核事实在运行 annotations；EditorService 的旧 suggestions/check 字段只是兼容残留或受控操作投影，新功能不据此管理检查生命周期。`RunManager.annotations()` 将原始检查范围映射到当前持久化稿；`sync_document()` 持久记录移动和待复查事实。局部采用通过当前 pending、文章身份、revision、状态和实际原文逐字检查后修改工作副本；历史动作被拒绝。

后端以 UTF-16 code unit 与 CodeMirror 一致，拒绝代理对内偏移。无法可靠定位、编辑命中与存在重复片段映射歧义时不采用或高亮。全文结构建议允许空 original。脱敏检测保留 checked_text，行号只用于该快照内片段定位，不直接作为当前正文坐标；每轮复查基于已确认稿。

`SourceEditor.tsx` 按 document_id 缓存 EditorState、编辑权限 compartment 和回调载体，切换后恢复选择、滚动与完整撤销/重做。外部采用、候选确认和恢复通过 isolateHistory 作为独立撤销步骤；预览只读，MDX/HTML 不执行。会话缓存不跨刷新，持久化草稿与版本独立于编辑撤销栈、运行 checkpoint。

已确认脱敏稿通过 RunManager 的 diff accept 自动安装，不再要求额外载入。其它文章生成稿通过 WorkflowDocumentBridge 显式对比载入；来源、候选 ID、revision 和冲突确认在后端核验，历史不能直接载入。正文在主编辑区处理，属性在侧栏。主编辑稿通过 final_review/save 的 document 与可选 editor 精确快照提交；服务端核验原文档、版本、全文和已采用的运行身份，在动作事务中同步输出基线。最终输出核对已提交版本，提交后再修改仍受保护，详见 [当前交互规则](../frontend-navigation.md)。

以上三个差异入口使用 `DocumentDiff` 比较固定的前后全文，显示红色删除、绿色新增与行内标记，默认折叠未改内容。脱敏基线取本轮 `checked_text`，兼容旧记录时依次回退到 `redacted_text`、`clean_text`、`raw_text`；冲突和载入各取自己的当前稿快照。前端不解析或展示后端原始 unified diff，因此旧预览截断不影响可视化全文对比。匹配预算和每次 100 行的展开只控制显示开销，不更改接受的候选文本。详见 [WF-R08](../workbench-and-workflows.md#工作流定义与运行)。
