# 6. 配置、密钥与副作用边界

## 6.1 配置 / 密钥流

```mermaid
flowchart TD
  Config[config.toml: blog / review / report]
  LLMConfig[llm.toml: llm provider / model / base_url]
  Env[指定 api_key_env 环境变量]
  LocalKey[.inkflow/secrets.json 本地密钥]
  Store[SettingsStore]
  Snapshot[任务启动时 snapshot]
  Public[GET settings: 仅 status / safe fields]
  Runtime[WorkflowRuntime: process-memory API key]
  Graph[LangGraph state / nodes]
  LLM[call_llm SDK adapter]
  Scrub[runtime.scrub: state/event/error 替换密钥]
  DB[(SQLite / event / report)]
  Rule[约束: key 不进入持久化 state]
  Model[OpenAI-compatible endpoint]
  Web[本机 React UI]
  TOMLKit[TOMLKit: 保留注释 / 原子更新]

  Config --> Store
  LLMConfig --> Store
  LocalKey -->|本地 key 优先| Store
  Env -->|本地 key 缺失时 fallback| Store
  Store --> Public --> Web
  Web -->|settings update| Store
  Store --> TOMLKit
  Store --> Snapshot --> Runtime
  Runtime --> Graph
  Runtime --> LLM --> Model
  Graph --> LLM
  Graph --> Scrub --> DB
  Rule -. protects .-> DB
```

## 6.2 配置和密钥管理

- `config.toml` 保存博客仓库、相对内容目录、review/report 目录；`llm.toml` 保存 `enabled/provider/model/base_url/api_key_env/temperature/timeout/max_tokens`。`SettingsStore` 用 TOMLKit 只改已知段，保留无关配置与注释；详情见 [配置模型](../../src/inkflow/settings.py#L24-L84)、[读取与保存](../../src/inkflow/settings.py#L114-L198)。
- provider 当前固定为 `openai-compatible`。`base_url` 必须为 HTTP(S)，不能嵌入用户名/密码/query/fragment；密钥单独保存在 `<data-dir>/secrets.json`，本地 key 优先于环境变量。见 [模型配置校验](../../src/inkflow/settings.py#L42-L65)、[key 读写](../../src/inkflow/settings.py#L139-L162)。
- 前端设置接口只回传“是否已配置/是否本地保存”，API schema 用 `SecretStr`，UI 输入是 password 字段。Key 文件是本机明文私有文件，不是系统密钥库；POSIX 权限 0600，Windows 用进程用户 SID 显式设置受保护 DACL，仅授权当前用户，临时文件在写入正文前设权，失败则终止并清理。读取旧密钥时也收紧权限。数据目录 POSIX 0700，Windows 使用仅当前用户的可继承 ACE；不改变配置目录和祖先权限。见 [文件权限](../../src/inkflow/services/file_permissions.py)、[原子写入](../../src/inkflow/services/local_files.py)、[密钥读写](../../src/inkflow/settings.py)。Windows API 语义参考 [SetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow)。
- 创建 Run 时取一次 settings snapshot；key 作为 runtime 依赖，不进入可持久化 `InkFlowState`。节点输出、interrupt payload 与错误摘要使用递归 scrub。见 [snapshot](../../src/inkflow/settings.py#L200-L213)、[WorkflowRuntime](../../src/inkflow/graphs/minimal.py#L81-L97)。这不代表原始文章本身不含敏感内容。

## 6.3 文件路径和发布目标

- 文章侧栏保留打开历史的真实路径树，不扫描目录。默认保存目录是浏览器偏好，初始沿用旧选择、配置目录或 `--workspace`；校验和修改只影响未命名稿首次保存建议位置，不登记工作区、迁移文件或改变运行。旧工作区服务继续供来源采集与兼容客户端使用。独立文件登记精确绝对位置，不授权整个父目录；后续按 document_id 校验文件与路径。工作区根以下仍拒绝隐藏路径、符号链接和越界；显式根目录本身可位于隐藏父目录，仅支持 UTF-8 `.md/.mdx/.markdown`。旧路径接口仍限 root 内，旧全局切换保留活跃运行限制和工作区请求头校验。见 [工作台选择规则](../workbench-and-workflows.md#工作台规则)、[EditorService](../../src/inkflow/web/editor.py)。
- 原生窗口由当前 Python 解释器启动独立子进程，通过标准库 Tk 选择路径；参数和结果使用 JSON，不执行 shell，不上传或复制文章。窗口在后端所在桌面显示，同一服务最多一个，300 秒超时关闭；缺少 Tk 或桌面会话时返回可操作错误，不自动安装依赖。设置选择仅填表，博客内容目录转换并限制为仓库内相对路径，仍需显式保存。见 [file_picker.py](../../src/inkflow/web/file_picker.py)。
- review/report 目录按配置文件目录解析。博客内容目录必须是仓库内相对路径且无 `..`；slug 安全化，拒绝逃逸路径和目标 symlink。见 [path resolve](../../src/inkflow/settings.py#L87-L112)、[publish destination](../../src/inkflow/services/publish.py#L12-L33)。
- 写博客前先预览目标；若文件存在，记录 SHA-256 fingerprint；最终确认时重新检查是否仍一致，再原子替换单个 `<slug>/index.mdx`。没有自动 Git/build/push、备份或回滚。见 [preview/write](../../src/inkflow/services/publish.py#L36-L63)。
- 普通 POSIX 文件原子替换保留原模式，新文件以 0666 创建并由内核应用 umask，不修改进程级 umask；密钥独立使用私有策略。博客构建/部署账户能否读取由文章和父目录的实际权限共同决定。
- 编辑器另有磁盘 hash 和 revision 双重保护：UI/服务冲突时拒绝静默覆盖；恢复历史只恢复到编辑区，必须再次显式保存。见 [EditorService.mutate](../../src/inkflow/web/editor.py#L193-L251)。

## 6.4 网络和内容处理界限

- Web 默认只绑定 `127.0.0.1`。FastAPI 限制可信 Host；有 Origin 时校验同源；写请求检查 cross-site 标记与 JSON Content-Type；POST/PUT body 逐块限制 8 MB。见 [Web host/body boundary](../../src/inkflow/web/app.py#L102-L131)、[启动监听](../../src/inkflow/web/__main__.py#L13-L43)。
- 这不是身份认证：代码没有用户登录/多租户授权层。不要绑定公网，也不要仅凭 localhost 认定为可托管的多用户服务。Origin 不存在时只凭当前代码不能推出请求已认证。
- 启用模型后，用户内容按模型业务路径发送到配置 provider；README 明确需用户确认内容隐私风险。模型供应方的数据保留/训练策略不在本仓库控制范围。
- 文章编辑预览不执行 HTML/MDX JavaScript；只允许安全链接形态并将图片显示为占位文本。实际生成的 MDX 最终仍需人工核对目标站点构建行为。见 [MarkdownPreview](../../frontend/src/MarkdownPreview.tsx#L4-L13)。
- 审阅稿、SQLite task payload、报告与 JSONL 都可能保存原文或模型结果，必须按敏感本地数据管理。报告服务本身明确允许完整内容用于复盘，见 [report.py](../../src/inkflow/services/report.py#L1-L5) 与 [报告字段](../../src/inkflow/services/report.py#L55-L84)。
- 新运行详情、SSE、产物及操作在运行锁内校验明确 document_id 和已登记资源、运行关联，SSE 逐批逐条重验；页面工作区选择不改变授权。旧无文档 ID 接口继续检查工作区，旧全局切换后关闭不匹配 SSE。无归属历史只读兼容；此处仍是本机单用户资源范围，不能当作多租户认证。

## 6.5 证据等级与建议确认

- **代码确认**：本机监听、Host/Origin 防护、输入长度、路径范围、密钥独立存储、密钥不进图 state、输出原子替换等实现均可在源码中定位。
- **结构推断**：这是本机单用户应用的安全模型；不能把路径白名单等同 OS 沙箱，也不能把 API key scrub 当作通用 PII 脱敏。
- **待人工确认**：模型服务的数据保留政策、主机磁盘加密、部署文件系统对模式位/DACL 的实际支持、密钥文件备份/清理策略、目标 Astro 项目的 CI 与最终 MDX 兼容性。文件权限不抵御管理员提权或磁盘离线访问。
- **容易误判**：LLM 脱敏 finding 不是强制防泄漏扫描；模型关闭时 findings 为空并不表示安全通过；降级占位文章依然可经过人工 final review；覆盖目标的 fingerprint 校验是普通单用户保护，不承诺抵抗其他进程恶意竞争写入。

## P2 配置归属变化

原 TOML 继续兼容，但仅作为流程初次迁移来源和显式导入草稿的默认值。模型参数随流程包固定；审阅稿、报告、博客分别归属 write_review、report、publish，路径基准不变。修改全局非密钥设置后，需导入流程草稿并启用；凭据仍按受控本地存储／环境变量引用解析。版本、历史、诊断与测试不保存真实密钥。节点诊断与模型试运行可能包含完整内容，保存在已有私有数据库，普通日志不输出正文。详见 [P2 配置和迁移规则](../workflow-assets.md)。

## P3 发布配置修订

PublishProfile 只提供 local-blog／Astro MDX／slug/index.mdx 与真实覆盖策略；共享配置以 generation 校验保存并产生不可变修订。定义节点显式采用修订，编译固定有效绝对目录，不能被页面工作区切换改变。没有工作区默认或运行时参数覆盖。模型参数和受控凭据引用按实例解析，凭据仅存运行内存。流程模拟在能力边界替代全部输出且不访问正式目标目录；静态计划不调用模型。内容目录及文章目标符号链接拒绝。详见 [P3 配置与保护](../workflow-definitions.md)。

P5 来源配置固定在任务中：公开 HTTP/RSS 与明确本机／内网访问分开，同源重定向、检查后的 DNS 地址固定连接、响应上限和退避独立于模型配置。HTTPS Bearer 仅引用 `INKFLOW_SOURCE_` 环境变量；项目来源仅读取已登记工作区的明确文本文件。来源不具备流程编辑、密钥读取或输出权限。详见 [来源边界](../automations.md#来源边界)。

## AI 编排与修复

AI 生成只读已授权配置引用，模型输出不能增加能力、凭据或目录。诊断发送前过滤模型／来源凭据；成功或未知效果拒绝关联重跑。声明式更新与执行代码发布分开，源码指纹及 P4 核验继续生效。详见 [P6 权限规则](../ai-orchestration.md)。
