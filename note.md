# 笔记

## 基本框架

### State

定义工作流状态结构，不同文件代表一种工作流所需的状态

可以通过下面几个问题决定字段是否需要记录

1. 这个数据是否需要被多个节点共享？
2. 这个数据是否需要从前一个节点传给后一个节点？
3. 这个数据是否会影响后续流程怎么走？
4. 最终结果里是否需要看到这个数据



### Graph

定义 `LangGraph `图：节点、边、执行顺序

每张图通常包含

1. 很多个节点函数
2. 一个 `build_graph()` 构建函数
3. 在 `build_graph()` 里创建 `StateGraph`
4. 把节点函数注册进图
5. 用边把节点连起来
6.  `compile()` 编译成可运行的图

> 图结构表达业务流程。
> 节点内部表达某个流程步骤的具体实现。
> 其它模块承载复杂能力，避免 `graph.py` 变得臃肿。

### Services

业务实现，一个节点对应一个业务代码，具体的实现在业务层中

负责节点所代表的能力的实现



### Prompts

负责管理提示词，也就是表达



### LLM

负责模型相关操作



`main.py`

启动流程，提供输入并运行图

> `LangGraph `的“骨架”靠 graph.py 里的节点和连线完成；
> 项目的“肌肉”可以放在节点调用的其它模块里；
> 什么时候改图，取决于你是不是新增了一个明确的流程阶段。



## 真实LLM调用



### 调用方式

可选不同的调用方式

- 云端 API：OpenAI、DeepSeek、通义千问、智谱、Anthropic 等
- 本地模型：Ollama、LM Studio、vLLM 等
- LangChain 封装：比如 ChatOpenAI / 兼容 OpenAI 接口的模型
- 直接 SDK：比如直接用某个厂商的 Python SDK







### 明确输入契约

1. 读取哪些文本
2. 返回什么文本

保证图节点之间互相解耦

### 错误处理

- API Key 没设置
- 网络失败
- 余额不足
- 请求超时
- 模型名写错
- 触发限流
- 返回内容为空

### 成本和上下文长度

- 输入文本太长会增加费用
- 模型有上下文窗口限制
- 草稿生成的输出长度也会产生费用
- 命令行反复运行会反复扣费

### 隐私处理

通过节点进行程序上的脱敏，但文本毕竟需要上传到第三方

- 是否允许发送原文
- 是否需要更严格的敏感信息过滤
- 是否要提醒用户输入中可能包含隐私
- 是否需要本地模型方案



### 同步调用还是流式输出



### 配置要留出拓展的空间



## 依赖

| 层级           | 代表依赖                        | 作用                           |
| :------------- | :------------------------------ | :----------------------------- |
| 模型 SDK       | openai、anthropic、google-genai | 直接调用模型 API               |
| LLM 抽象框架   | langchain、langchain-openai     | 统一不同模型的调用接口         |
| 编排框架       | langgraph                       | 管理多节点工作流状态           |
| Web API 框架   | fastapi、uvicorn                | 把能力暴露成 HTTP 服务         |
| RAG/知识库框架 | llama-index、向量库             | 文档检索、知识增强             |
| LLM 网关       | litellm                         | 多模型路由、统一接口、成本统计 |
| 配置/环境变量  | tomllib、python-dotenv          | 读取配置、加载密钥             |





## LLM使用

设置开关

提示词放置在prompt中



## 工具调用

工具调用的本质是：

> **LLM 负责“想做什么”，你的程序负责“能不能做、怎么做、做完返回什么”。**

### 基本概念

LLM本身没有读文件、搜索代码、执行命令的能力，只能靠猜

而工具调用的流程是这样的

```
用户：帮我看这个项目哪里有 bug
LLM：我需要先搜索代码，调用 search_text("TODO|bug|error")
你的程序：执行搜索，把结果返回给 LLM
LLM：我需要读取 src/main.py
你的程序：读取文件片段，把内容返回给 LLM
LLM：分析后给出答案
```

所以工具调用的本质是一个结构化请求

```json
{
  "tool": "read_file",
  "arguments": {
    "path": "src/main.py",
    "start_line": 1,
    "limit": 100
  }
}
```

真正执行的是程序

### 架构设计

```
LLM
负责推理、规划、决定要调用什么工具

Agent Host
你写的中间层程序，负责管理对话、校验权限、执行工具、返回结果

Tools
具体能力，例如读文件、搜索文本、执行命令、修改文件
```

流程如下

```
用户任务
  ↓
Agent Host 把任务和可用工具发给 LLM
  ↓
LLM 返回普通回答或 tool_call
  ↓
Agent Host 检查这个 tool_call 是否安全、是否允许
  ↓
Agent Host 在本地执行工具
  ↓
执行结果作为 observation 返回给 LLM
  ↓
继续循环，直到 LLM 输出最终回答
```

### 核心工具

```
list_files(path)
read_file(path, start_line, limit)
search_text(query, path)
run_command(cmd, cwd)
apply_patch(patch)
```

1. `list_files`

   让模型知道项目里有哪些文件

   ```json
   {
     "tool": "list_files",
     "arguments": {
       "path": "."
     }
   }
   // 返回
   {
     "files": [
       "README.md",
       "package.json",
       "src/main.ts",
       "src/utils.ts"
     ]
   }
   ```

2. `read_file`

   读取文件的一部分

   ```json
   {
     "tool": "read_file",
     "arguments": {
       "path": "src/main.ts",
       "start_line": 1,
       "limit": 120
     }
   }
   //返回
   {
     "path": "src/main.ts",
     "start_line": 1,
     "end_line": 120,
     "content": "..."
   }
   ```

   可以

   - 避免上下文爆炸
   - 避免超长文件拖慢模型
   - 方便模型逐段分析
   - 更安全，减少泄露敏感内容的风险

3. `search_text`

   让模型在项目里搜索关键词，内部可以调用`rg`即`ripgrep`

   ```json
   {
     "tool": "search_text",
     "arguments": {
       "query": "createUser",
       "path": "src"
     }
   }
   ```

   程序执行

   ```bash
   rg -n "createUser" src
   ```

   返回

   ```json
   {
     "matches": [
       {
         "path": "src/user.ts",
         "line": 18,
         "text": "export function createUser(...)"
       },
       {
         "path": "src/api.ts",
         "line": 42,
         "text": "const user = createUser(data)"
       }
     ]
   }
   ```

   一个好的coding agent通常的流程是

   ```
   先搜索 → 再读相关文件 → 再分析 → 再修改
   ```

4. `run_command`

   执行命令，比如测试、构建、类型检查

   ```json
   {
     "tool": "run_command",
     "arguments": {
       "cmd": "npm test",
       "cwd": "."
     }
   }
   //返回
   {
     "exit_code": 1,
     "stdout": "...",
     "stderr": "TypeError: Cannot read property ...",
     "timed_out": false
   }
   ```

   这让LLM可以根据真实报错继续修复，但是需要严格校验

5. `apply_patch`

   修改文件

   比起直接让模型能自由的`write_file(path, content)`导致难以审计

   更好的方式是让模型输出patch:

   ```diff
   *** Begin Patch
   *** Update File: src/main.ts
   @@
   - const result = foo(input)
   + const result = await foo(input)
   *** End Patch
   ```

   程序可以检查patch是否安全，再应用

   - 可以展示diff
   - 可以人工确认
   - 可以回滚
   - 不容易误覆盖整个文件

#### 工具为什么要用JSON Schema

比起跟llm说“你可以读文件，需要时告诉我你要读什么。”

更好的是定义工具

```json
{
  "name": "read_file",
  "description": "Read a UTF-8 text file inside the workspace.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string"
      },
      "start_line": {
        "type": "integer"
      },
      "limit": {
        "type": "integer"
      }
    },
    "required": ["path"]
  }
}
```

这样模型只能生成结构化参数

```json
{
  "path": "src/main.py",
  "start_line": 1,
  "limit": 100
}
```

然后程序负责

- 解析参数
- 校验路径
- 判断权限
- 执行工具
- 截断输出
- 返回结果

重点在于

模型提出请求，你的程序决定是否执行。

不过有个问题，工具需要进行注册，在底层输入



### 文件沙箱

需要设置workspace，避免通过连续的`../../`跳出当前工作目录访问不应该访问的内容

正确做法应该是

1. 把 workspace 解析成绝对路径
2. 把模型请求的 path 也解析成绝对路径
3. 检查目标路径是否仍然在 workspace 内
4. 如果不在，拒绝

伪代码

```python
from pathlib import Path

workspace = Path("/Users/me/project").resolve()

def safe_path(user_path: str) -> Path:
    target = (workspace / user_path).resolve()

    if not str(target).startswith(str(workspace)):
        raise PermissionError("Path escapes workspace")

    return target
```

还可以禁止敏感文件

### 命令执行

一个可靠的`run_command`不应该只接受cmd

至少有

```json
{
  "cmd": "npm test",
  "cwd": ".",
  "timeout_seconds": 30,
  "max_output_bytes": 20000
}
```

返回

```json
{
  "exit_code": 1,
  "stdout": "...",
  "stderr": "...",
  "duration_ms": 1234,
  "timed_out": false,
  "truncated": false
}
//exit_code：告诉模型命令成功还是失败
//stdout：正常输出
//stderr：错误输出
//timeout：防止命令卡死
//max_output_bytes：防止超长日志塞爆上下文
//cwd：防止在错误目录执行
```

例如测试失败后，LLM 可以看到：

```
exit_code = 1
stderr = TypeError: user.name is undefined
```

然后继续搜索 `user.name`，读取相关代码，提出修复。

### 最小实现

```python
messages = [
    {
        "role": "system",
        "content": "你是一个本地编码 agent。需要先理解代码，再修改。"
    },
    {
        "role": "user",
        "content": "帮我修复测试失败的问题"
    }
]

while True:
    response = call_llm(
        messages=messages,
        tools=[
            list_files_schema,
            read_file_schema,
            search_text_schema,
            run_command_schema,
            apply_patch_schema
        ]
    )

    if response.type == "final":
        print(response.text)
        break

    for tool_call in response.tool_calls:
        if not permission_allowed(tool_call):
            result = {
                "error": "Permission denied",
                "reason": "This command requires approval"
            }
        else:
            result = execute_tool(tool_call)

        messages.append({
            "role": "assistant",
            "tool_call": tool_call
        })

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result)
        })
```

> append是在上下文后面追加聊天记录
>
> 模型本身近似无状态；所谓记忆，是你的应用在每次请求前，把相关历史、摘要、文件内容、工具结果重新组织后塞进上下文。

### 提示词

不宜太复杂，但是要规定行为边界

```
你是一个本地编码 agent。

你可以使用工具读取文件、搜索文本、运行命令、应用 patch。
在修改代码前，必须先理解现有代码。
优先使用 search_text 和 read_file，不要猜测文件内容。
不要访问 workspace 外的文件。
执行破坏性命令前必须请求用户确认。
命令失败时，要根据 exit_code、stdout、stderr 分析原因。
修改代码后，尽量运行相关测试。
```

### 完整示例

用户说：

```
帮我修复登录失败的问题
```

Agent 可能这样工作：

#### 第一步：搜索相关代码

```
{
  "tool": "search_text",
  "arguments": {
    "query": "login",
    "path": "."
  }
}
```

工具返回：

```
src/auth.ts:12 function login(...)
src/api/login.ts:8 router.post("/login", ...)
tests/auth.test.ts:22 test("login succeeds")
```

#### 第二步：读取测试

```
{
  "tool": "read_file",
  "arguments": {
    "path": "tests/auth.test.ts",
    "start_line": 1,
    "limit": 120
  }
}
```

#### 第三步：读取实现

```
{
  "tool": "read_file",
  "arguments": {
    "path": "src/auth.ts",
    "start_line": 1,
    "limit": 160
  }
}
```

#### 第四步：运行测试

```
{
  "tool": "run_command",
  "arguments": {
    "cmd": "npm test -- auth.test.ts",
    "cwd": "."
  }
}
```

返回：

```
Expected token to be defined, received undefined
```

#### 第五步：修改代码

```
{
  "tool": "apply_patch",
  "arguments": {
    "patch": "*** Begin Patch\n..."
  }
}
```

#### 第六步：再次测试

```
{
  "tool": "run_command",
  "arguments": {
    "cmd": "npm test -- auth.test.ts",
    "cwd": "."
  }
}
```

#### 第七步：最终回答

```
已修复登录逻辑中 token 没有返回的问题。
修改了 src/auth.ts。
相关测试已通过。
```



### MCP是什么

“给 agent 暴露工具的一套标准协议”

不使用MCP的时候，由Agent Host实现各个工具

使用MCP时

```
Agent Host
  ↔ MCP filesystem server
  ↔ MCP shell server
  ↔ MCP git server
  ↔ MCP database server
```

好处是可以复用，但是以学习为考虑的话，不应该一上来就使用



