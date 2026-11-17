# Web 运行与持久化

运行以文章为归属，详细规则与接口见[统一工作台](../workbench-and-workflows.md#工作流定义与运行)。

ProcessLock 保证同数据目录只有一个服务进程。RunManager 以 RLock 串行校验和更新状态，每次运行有独立 graph、SQLiteSaver、冻结配置与进程内密钥。线程池最多四个执行线程，人工中断后释放线程；排队执行、running、waiting、stopping 均占文章名额。创建时解析文档资源 ID 并校验 revision 与输入，内存校验加 SQLite `article_active` 唯一键防止并发绕过。终态事件与名额删除同事务保存。

| 表 | 当前职责 |
| --- | --- |
| runs | 工作流/图快照、文章归属、输入、状态、待办、执行、标注审核事实、产物与事件游标 |
| article_active | document_id 唯一文档键，对应一个活跃 run_id |
| events | 每次运行有序 seq 事件，与运行状态同事务提交 |
| actions | request_id、请求 hash 与结果，供重复操作幂等校验 |
| documents / document_aliases | 精确文件位置、来源 ID 与旧身份映射 |
| workspaces | 多工作区 ID 与根目录登记 |
| document_copies / document_versions | 当前工作副本、磁盘基线与不可变版本 |
| editor_docs | 保留的旧工作副本快照，不再承接新写入 |
| editor_versions | 保留的迁移前旧版本；复制至新版本表 |
| editor_tasks | 旧独立检查兼容数据，新检查不写入、不批量迁移 |

人工操作先校验终态、pending_id 和 request_id；全文检查建议审核保持 waiting，只更新工作副本与审核事实，finish_check 才恢复图到终点。脱敏 diff accept 先安装已确认稿再恢复图，候选与工作副本冲突时保留待办。停止在安全节点边界生效，已输出文件不回滚。

SSE 接收 after 与 Last-Event-ID，按 seq 补发 run_event，终态追平后发 end；前端收到事件后重取详情。新详情、动作、文稿对比/载入、SSE 与产物在运行锁内校验明确 document_id 与登记资源及运行归属；页面切换不改变授权。SSE 每批读取及逐条发送前复查。旧无 ID 接口继续使用当前工作区校验，跨工作区返回 404，过期工作区头返回 409，旧 SSE 切根后关闭。新 `GET /document-runs` 每 1.2 秒供已打开文章刷新摘要，历史按文档 ID 查询；旧 article-runs 和默认历史保留旧工作区范围，unlinked=true 单独只读查询。

P4 正式运行重启恢复固定检查点和原待办，未知效果先核验；仅旧无协议运行标记 interrupted 并释放名额。重新运行基于当前文章稿件与新配置创建独立实例。旧无工作流标识记录读取时归入默认图，无文章归属记录只读查看；不猜测或批量重写用户历史。

任务产物只能按已登记 ID 从 `<data-dir>/artifacts/<run-id>/` 下载。源文章、审阅稿、报告和博客输出仍是文件；密钥不进入 SQLite、事件或报告。

历史含完整文章，数据目录在 POSIX 下设为 0700，打开数据库前将数据库及残留 WAL/SHM/journal 收紧为 0600；SQLite 从私有数据库模式创建新日志。Windows 目录使用仅当前用户的可继承 DACL，数据库也显式收紧。只修改应用数据目录，不调整其配置目录或祖先权限。

不同文章并发输出同一博客目标时，进程内输出锁将指纹核验与原子替换作为一个临界区，防止本服务的两个线程绕过覆盖保护。

## P2 版本与诊断边界

新增资产、版本指针、持久递增草稿 revision、节点尝试与独立模型测试表，旧运行／事件／动作记录保留。RunManager 在创建时固定 WorkflowCatalog 的版本包；WorkflowRuntime 统一渲染、模型调用、解析与诊断。诊断详情按运行和 document_id 校验，只读数据库中对应尝试，不读客户端路径。完整字段按需加载；旧运行提示词版本未知。版本保存不恢复 LangGraph 检查点，模型测试重启也标记中断。详见 [P2 当前行为](../workflow-assets.md)。

## P3 定义、配置与模拟

新增 workflow_registry、publish_profiles、publish_profile_revisions、definition_tests；原版本库同时读取格式 1 内置包和格式 2 定义／编译包。新流程运行仍进入 RunManager，绑定完整编译包，事件、待办和诊断补充实例／尝试／文档归属。产物以节点实例区分。运行中心仅查询已登记文档与明确工作区范围，详情与动作继续使用原资源校验。流程模拟独立身份但共用编译和执行器，在能力边界替换副作用，不占正式文章名额。正式运行按 P4 恢复，模拟和测试仍中断。详见 [P3 边界](../workflow-definitions.md)。

## P4 执行协议

工作队列、进程锁和租约、同步检查点、节点结果、动作与工作副本原子接收、模型回执、文件效果及剩余窗口详见 [P4 当前行为](../durable-execution.md)。新增六张 execution 表保留旧事实；恢复受阻与等待核验仍占文章名额。

P5 增加来源计划、证据、检查、变化积压和同源限流表。来源快照／变化项／游标同事务提交，运行派发通过固定 creation_id 恢复确认；RunManager 增加内部固定版本和 automation 输入，外部普通创建接口不能伪造来源上下文。详见 [P5 事务边界](../automations.md#持久事实积压和恢复)。

## AI 编排与修复

P6 新增 ai_proposals 保存请求、模型分析、差异、静态错误、固定样例及原／新模拟引用。采用和启用沿用资产事务；修复运行的 repair_of 在创建事务内保存，原效果只读。详见 [P6 数据与恢复](../ai-orchestration.md)。
