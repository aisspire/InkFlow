# InkFlow 文档索引

当前行为以源码和 [统一工作台与工作流](workbench-and-workflows.md) 为准。

前端四区导航、URL 与会话恢复、统一审阅及 AI 分步界面见 [应用导航与文章审阅](frontend-navigation.md)。该文替代旧资料中以全屏弹窗打开工作流／任务／设置的界面描述，P1–P6 业务和后端保护继续有效。

多场景入口与下一阶段拆分见 [架构演进方案](inkflow-architecture/07-workspace-workflow-evolution.md)。该文档的 **P1–P6 已实施**；提示词版本、诊断与仅脱敏见 [P2 当前行为](workflow-assets.md)。[P3 定义编排与发布配置](workflow-definitions.md) 已实现；[P4 持久执行与恢复](durable-execution.md) 已实现；[P5 持续关注与来源连接](automations.md) 已实现；[P6 AI 编排与修复](ai-orchestration.md) 已实现。当前规则、迁移和兼容边界见统一工作台文档。

| 本次任务 | 优先阅读 |
| --- | --- |
| 页面导航、URL、切页会话、任务能力与统一候选审阅 | [当前前端交互](frontend-navigation.md)、[前端组件](inkflow-architecture/05-frontend-map.md) |
| 文章路径树、侧栏移除与找回、最近记录清理、草稿删除、默认保存目录、另存为与失联恢复 | [统一工作台规则](workbench-and-workflows.md#工作台规则)、[当前前端交互](frontend-navigation.md)、[前端组件](inkflow-architecture/05-frontend-map.md) |
| 全文检查、文章单活、运行归属、API / CLI | [工作流边界与接口](workbench-and-workflows.md#工作流定义与运行)、[Web 持久化](inkflow-architecture/03-web-runs-and-storage.md) |
| AI 创建、失败诊断、差异草稿、固定样例与关联修复运行 | [P6 当前规则](ai-orchestration.md)、[专项回归](../tests/test_ai.py)、[进程恢复](../tests/test_ai_recovery.py) |
| 来源采集、定时检查、变化证据、积压与持续候选 | [P5 当前行为](automations.md)、[P5 验收](../tests/test_automation.py) |
| 服务重启、人工待办、输出效果核验、迁移与回退 | [P4 当前行为](durable-execution.md)、[恢复回归](../tests/test_recovery.py) |
| 用户流程创建／复制、节点连接、发布配置、模拟与运行中心 | [P3 当前行为和迁移](workflow-definitions.md) |
| 提示词编辑、流程草稿／启用／回退、试运行、诊断与仅脱敏 | [P2 资产与诊断](workflow-assets.md) |
| 处理、可视化差异审阅、生成与输出分支 | [工作流与差异规则](workbench-and-workflows.md#工作流定义与运行)、[现有内容处理图](inkflow-architecture/02-content-workflow.md) |
| 文章确认的版本冲突与重新审阅 | [P3 交互与文档保护](workflow-definitions.md#交互文档与输出) |
| 原文、建议定位、版本与磁盘保护 | [编辑闭环](inkflow-architecture/04-editor-workbench.md) |
| 配置、密钥与本机边界 | [配置边界](inkflow-architecture/06-configuration-and-boundaries.md) |
| 多场景入口、对象分层、提示词编辑、持续任务、热更新及分阶段改造 | [架构演进方案（P1–P6 已实施）](inkflow-architecture/07-workspace-workflow-evolution.md) |
| CLI、工作区读取、SSE 与跨平台文件权限回归 | [验证方法](workbench-and-workflows.md#验证与范围)、[基础回归](../tests/test_regressions.py)、[P1 身份与迁移回归](../tests/test_documents.py) |
| 实施取舍和本轮验证 | 根目录 `implementation-notes.md` |

原 `2026-11-06_17-28_7c66825a_项目文档.md` 是本地历史快照，其中两个工作台切页与单一硬编码流程的描述已由当前文档替代。技术栈继续采用 [technology-decisions.md](technology-decisions.md)。本索引、统一工作台规则、技术选型和架构地图随源码交付；其它本地资料及根目录实施记录继续忽略。
