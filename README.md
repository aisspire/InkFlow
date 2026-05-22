# InkFlow

InkFlow 是一个用于学习 LangGraph 的半自动内容发布工作流项目。它把一份本地 Markdown 或文本输入，依次经过脱敏审查、人工确认、文章生成、Astro Markdown 拼装、本地审阅、可选发布和完整报告记录。

这个项目当前更偏学习和流程验证：每个节点都尽量保持清楚，方便观察 LangGraph 状态如何在节点之间流转，也方便以后继续扩展 RAG、事实检查、Web 审阅界面或更多内容来源。

## 快速运行

项目使用 Python 3.11 和 `src` 目录布局。请在项目根目录按模块方式启动，不要直接运行 `src/inkflow/main.py`。

```powershell
$env:PYTHONPATH = "src"
python -m inkflow.main --input note.md
```

只生成审阅稿和报告，不执行博客仓库复制、构建、commit 或 push：

```powershell
$env:PYTHONPATH = "src"
python -m inkflow.main --input note.md --no-publish
```

使用配置文件里的默认输入：

```powershell
$env:PYTHONPATH = "src"
python -m inkflow.main
```

指定另一份配置文件：

```powershell
$env:PYTHONPATH = "src"
python -m inkflow.main --config config.toml
```

如果还没有安装依赖，可以先运行：

```powershell
pip install -e .
```

## 当前工作流

默认命令会运行完整 LangGraph 流程：

```text
读取输入
  -> 基础预处理
  -> 生成脱敏审查方案
  -> 终端逐条确认
  -> 必要时执行脱敏并展示 diff
  -> 重新复查脱敏结果
  -> 生成结构化文章 JSON
  -> 拼装 Astro Markdown
  -> 写入本地审阅稿
  -> 用户接受后发布到静态博客仓库
  -> 写入 JSONL 审计日志和 Markdown 报告
```

如果传入 `--no-publish`，审阅稿被接受后会直接进入报告节点，发布节点不会执行。

## 终端交互

### 脱敏方案审查

程序会展示 LLM 发现的敏感项，包括风险等级、行号、问题、原因、原文片段和建议。

- `s`：停止流程，进入报告节点。
- `n`：忽略当前敏感项，不执行修改。
- 直接回车：采用 LLM 给出的建议。
- 输入其它内容：把这段内容作为你的最终修改方案。

所有敏感项处理完后，还需要输入：

- `y`：进入下一步。
- `s`：停止流程，进入报告节点。

### 脱敏 diff 确认

当存在需要执行的脱敏决策时，程序会让 LLM 返回完整改写文本，并展示 unified diff。

- `y`：接受本次修改，并回到脱敏方案节点重新复查。
- `n`：不接受本次 diff，按原决策重新尝试。
- `s`：停止流程，进入报告节点。
- 输入其它内容：作为额外建议，让 LLM 重新改写。

### 文章生成追问

文章生成节点会让 LLM 输出结构化 JSON。模型如果认为信息不足，可以向用户追问，最多追问 3 次。每次问答都会写入审计事件。

如果没有启用 LLM，或文章生成失败，程序会降级为本地占位文章，用来继续验证后续 Markdown 拼装、审阅稿和报告流程。

### 审阅稿确认

程序会把拼装后的 Astro Markdown 写入 `reviews/`，然后等待用户选择：

- `y`：接受审阅稿。普通模式会进入发布节点；`--no-publish` 模式会直接进入报告节点。
- `d`：回到脱敏阶段。
- `g`：回到文章生成阶段。
- `s`：停止流程，进入报告节点。
- 输入其它内容：作为修改建议，回到文章生成阶段。

## 配置

当前默认配置在 `config.toml`：

```toml
input_path = "note.md"

[review]
dir = "reviews"

[blog]
repo_path = ""
content_dir = "src/content/blog"

[publish]
build_command = "npm run build"
commit_message_template = "publish: {title}"

[report]
dir = "reports"
```

配置说明：

- `input_path`：默认输入文件。命令行 `--input` 会覆盖它。
- `review.dir`：审阅稿输出目录。相对路径按配置文件所在目录解析。
- `blog.repo_path`：静态博客仓库路径。留空时发布节点会失败并写入报告，不会猜测目标目录。
- `blog.content_dir`：博客仓库内的 Astro 内容根目录。发布时会在这个目录下创建文章 slug 文件夹，并写入 `index.mdx`。
- `publish.build_command`：复制文章后执行的构建检查命令。留空时跳过构建检查，只执行 `git add`、`git commit` 和 `git push`。
- `publish.commit_message_template`：发布提交信息模板，可使用 `{title}`。
- `report.dir`：报告输出目录。相对路径按配置文件所在目录解析。

## LLM 配置

真实模型配置使用 `llm.toml`。先复制示例文件：

```powershell
Copy-Item llm.example.toml llm.toml
```

然后按你的服务修改 `[llm]` 配置，并设置 `api_key_env` 指向的环境变量。`llm.toml` 已在 `.gitignore` 中忽略，不要提交真实密钥或私有模型配置。

当前第一版只支持 OpenAI-compatible 接口。`enabled = false` 时，脱敏 findings 会返回空列表，文章生成会使用本地占位文章，方便离线学习流程。

## 发布行为

用户在审阅稿阶段选择 `y`，且没有传入 `--no-publish` 时，发布节点会按固定顺序执行：

1. 把审阅稿复制到 `blog.repo_path / blog.content_dir / <slug> / index.mdx`。
2. 如果 `publish.build_command` 非空，在博客仓库运行构建检查命令。
3. 运行 `git add <published_file>`。
4. 运行 `git commit -m "publish: {title}"`。
5. 运行 `git push`。

`<slug>` 来自文章 JSON 里的 `slug` 字段。模型会被要求生成朴素的小写英文 slug，只允许英文、数字和连字符；程序发布前还会再清洗一次。发布命令由程序按固定列表组装，LLM 不参与生成命令。任一步失败后，后续命令不会继续执行，失败结果会写入 `publish_log` 和报告。

Astro frontmatter 中的 `tags` 和 `authors` 会输出为单行数组，例如 `tags: ['LangGraph', '工作流', 'LLM', '笔记']` 和 `authors: ['huijue']`。`authors` 是必填字段；模型未返回时程序默认使用 `huijue`。

## 报告和安全边界

每次流程结束会尽量生成两类本地报告：

- `reports/<run_id>.jsonl`：完整审计事件，每行一条 JSON。
- `reports/<run_id>.md`：人类可读的流程报告。

报告会保存敏感内容、脱敏方案、用户选择、LLM 输出、diff、最终 Markdown、发布目标路径和命令返回结果。`reports/` 和 `reviews/` 可能包含不适合公开的内容，提交公开仓库前请人工确认，不要把敏感审计记录、审阅稿或真实博客发布日志提交出去。

## 项目结构

```text
src/inkflow/main.py              命令行入口，读取配置和输入文件
src/inkflow/graphs/minimal.py    LangGraph 节点和路由
src/inkflow/state/workflow.py    工作流共享状态类型
src/inkflow/services/            脱敏、文章生成、包装、审阅、发布、报告等服务
src/inkflow/prompts/             LLM prompt 组装
config.toml                      本地工作流配置
llm.example.toml                 LLM 配置示例
```

## 后续方向

- 增加本地知识库，用于去重、风格参考和历史文章上下文检索。
- 增加事实检查和引用完整性检查。
- 把终端审阅升级为更清楚的 Web 审阅界面。
- 为发布前检查增加更细的安全开关和回滚记录。
