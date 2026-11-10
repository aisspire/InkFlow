# Web 运行与持久化

运行以文章为归属，详细规则与接口见[统一工作台](../workbench-and-workflows.md#工作流定义与运行)。

ProcessLock 保证同数据目录只有一个服务进程。RunManager 以 RLock 串行校验和更新状态，每次运行有独立 graph、InMemorySaver、冻结配置与进程内密钥。线程池最多四个执行线程，人工中断后释放线程；排队执行、running、waiting、stopping 均占文章名额。创建时规范化文章路径并校验 revision 与输入，内存校验加 SQLite `article_active` 唯一键防止并发绕过。终态事件与名额删除同事务保存。

| 表 | 当前职责 |
| --- | --- |
| runs | 工作流/图快照、文章归属、输入、状态、待办、执行、标注审核事实、产物与事件游标 |
| article_active | 工作区与路径编码后的唯一文章键，对应一个活跃 run_id |
| events | 每次运行有序 seq 事件，与运行状态同事务提交 |
| actions | request_id、请求 hash 与结果，供重复操作幂等校验 |
| editor_docs | 当前持久化工作副本与磁盘基线 |
| editor_versions | 全量文章版本，包含替换前后快照 |
| editor_tasks | 旧独立检查兼容数据，新检查不写入、不批量迁移 |

人工操作先校验终态、pending_id 和 request_id；全文检查建议审核保持 waiting，只更新工作副本与审核事实，finish_check 才恢复图到终点。脱敏 diff accept 先安装已确认稿再恢复图，候选与工作副本冲突时保留待办。停止在安全节点边界生效，已输出文件不回滚。

SSE 接收 after 与 Last-Event-ID，按 seq 补发 run_event，终态追平后发 end；前端收到事件后重取详情。详情、动作、文稿对比/载入、SSE 与产物均在运行锁内校验运行归属当前工作区，不依赖客户端是否发送工作区头；跨工作区 ID 返回 404，旧页面携带过期工作区头返回 409。SSE 在每批读取及每条事件发送前重新校验，切换后关闭流。`GET /article-runs` 每 1.2 秒供左侧所有文章刷新摘要；历史列表默认限定当前工作区，旧未关联历史使用 unlinked=true 单独只读查询。

重启将 running/waiting/stopping 标为 interrupted、清 pending 和名额，保留状态与历史，不恢复内存 checkpoint。重新运行基于当前文章稿件与新配置创建独立实例。旧无工作流标识记录读取时归入默认图，无文章归属记录只读查看；不猜测或批量重写用户历史。

任务产物只能按已登记 ID 从 `<data-dir>/artifacts/<run-id>/` 下载。源文章、审阅稿、报告和博客输出仍是文件；密钥不进入 SQLite、事件或报告。

历史含完整文章，数据目录在 POSIX 下设为 0700，打开数据库前将数据库及残留 WAL/SHM/journal 收紧为 0600；SQLite 从私有数据库模式创建新日志。Windows 目录使用仅当前用户的可继承 DACL，数据库也显式收紧。只修改应用数据目录，不调整其配置目录或祖先权限。

不同文章并发输出同一博客目标时，进程内输出锁将指纹核验与原子替换作为一个临界区，防止本服务的两个线程绕过覆盖保护。
