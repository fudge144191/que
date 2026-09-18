# Mini Agent（SAST 免试题 5：从零实现一个会使用工具的 Mini Agent）

从零实现的一个小型 Agent Harness：模型决定下一步做什么，程序负责执行工具、把关权限，再把结果交还给模型——这个不断重复的过程就是 **Agent Loop**。

本项目在题目提供的 starter 之上补全实现，保留了 `contracts.py` 的基本接口，公开测试可直接运行。

> 答辩/面试口径（为什么这么设计、被追问怎么答）见 [`docs/DEFENSE.md`](docs/DEFENSE.md)。

## 一、项目简介

- 语言：Python 3.11+（运行时零第三方依赖，仅开发依赖 `pytest`）
- 交互方式：命令行：`python -m mini_agent.cli "<问题>"`
- 模型：内置 `FakeModel` 离线回放（无需 API Key）即可跑通全流程；另接入 OpenAI 兼容接口作为真实模型
- 未使用 LangChain / LangGraph / CrewAI / AutoGen 等现成 Agent 框架，Agent Loop 全部手写

核心思想一句话：**把"模型不可信"当成前提**。Harness 的每一道关口都在把不可信的输入（乱填的参数、越界的路径、不存在的工具、超长输出）变成模型能读懂的反馈，而不是让程序崩溃。

## 二、运行与测试

```bash
# 1. 测试（pyproject 已配置 pythonpath=src）
python -m pytest -q

# 2. 离线演示：无需 API Key，按脚本回放模型回复
PYTHONPATH=src python -m mini_agent.cli \
  "阅读 workspace/materials 的资料，找出与 Agent Loop 有关的内容并生成一份总结。" \
  --root workspace \
  --model-backend script --script examples/demo_replies.json

# 3. 真实模型（任意 OpenAI 兼容端点）
export OPENAI_API_KEY=...
PYTHONPATH=src python -m mini_agent.cli "workspace 里有什么?" --root workspace
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--root` | 工作区根目录，工具的路径都锁在里面（默认 `.`） |
| `--max-steps` | 模型调用预算，超过即 `max_steps` 停止（默认 8） |
| `--trust-all` | 跳过确认，全部放行（默认：只读放行、写操作询问） |
| `--no-trace` | 隐藏逐步轨迹 |
| `--show-messages` | 打印最终中立消息历史 |
| `--model-backend` | `openai`（默认）/ `script`（离线回放）/ `none` |
| `--model-name` `--base-url` `--api-key` | 真实模型配置 |
| `--session ID` | 会话持久化：存在则续接，结束时把历史存回该文件 |
| `--list-sessions` | 列出已保存的会话 |
| `--sessions-dir` | 会话文件目录（默认 `.sessions`） |
| `--max-context-chars` | 历史字符预算，超出则压缩较早消息；`0` 关闭（默认 20000） |

安装为命令（可选）：`pip install -e .` 后可直接 `mini-agent "..."`，无需 `PYTHONPATH`。

## 三、已实现功能

对照题目「题目要求」四项：

| 要求 | 实现 |
| --- | --- |
| 1. Agent Loop | `agent.py`：发请求带工具清单 → 模型回答或调用工具 → 执行并回灌结果 → 循环；有 `max_steps` 上限，停于「最终回答 / 步数超限 / 模型异常」三种状态 |
| 2. 工具系统（≥3 类） | 文件系统 `read_file` `list_dir` `file_exists`；检索 `search_text` `find_files`；计算与系统 `calculator` `now` `echo`；会修改状态的 `write_file`。新增工具只需 `ToolRegistry.register()`，Loop 无需改动 |
| 3. 安全与错误处理 | 路径先 `resolve()` 再判断是否落在 `workspace/` 内（`../`、绝对路径越界、指向外部的符号链接一律拒绝）；写操作走权限确认，只读直接执行；未知工具 / 错误参数 / 执行失败 / 权限拒绝 / 步数超限 / 模型异常均转成工具结果回灌，程序不崩 |
| 4. 模型与测试 | `FakeModel` 全流程可用；真实模型通过 `ModelClient` 协议独立接入，Loop 内无厂商逻辑；35 个单元测试（含题目要求的六类异常场景、沙箱、并行、压缩、会话）+ 公开测试全部通过 |

额外完成四个拓展任务：并行只读工具、过长工具输出截断、对话上下文压缩、会话持久化（见第六节）。

## 四、基本设计

### 4.1 分层

| 文件 | 职责 |
| --- | --- |
| `src/mini_agent/agent.py` | Agent Loop：消息历史、工具广播、五道关、停止条件 |
| `src/mini_agent/contracts.py` | 题目提供的接口定义（未改动） |
| `src/mini_agent/validation.py` | 零依赖 JSON Schema 子集校验 |
| `src/mini_agent/permissions.py` | 权限策略：`AllowAll` / `DenyAll` / `WhitelistPolicy` / `ConsequentialPolicy` / `AskUserPolicy` |
| `src/mini_agent/tools/` | `ToolRegistry` + 三类内置工具（filesystem / search / system） |
| `src/mini_agent/trace.py` | 轨迹事件流与渲染 |
| `src/mini_agent/models.py` | OpenAI 兼容客户端 + 离线脚本回放 |
| `src/mini_agent/context.py` | 上下文压缩：超预算时缩短较早消息正文，不删消息 |
| `src/mini_agent/session.py` | 会话持久化：消息历史的保存 / 恢复 / 列出 |
| `src/mini_agent/cli.py` | CLI 入口 |

关键切分：**模型只负责选，Harness 只负责做**。因此 `Agent` 只依赖 `ModelClient` 协议，不依赖任何 SDK；`PermissionPolicy` 也是注入的，测试里永远不需要 `input()`。

### 4.2 Agent Loop 流程

```
run(query)
  ├─ 建消息历史 [user: query]         （默认不加 system，公开测试断言 messages[0] 就是用户消息）
  ├─ specs = registry.specs()         （每轮把工具清单发给模型）
  └─ while steps < max_steps:
       ├─ model.complete(深拷贝 messages, specs)
       │    └─ 抛异常 → status="model_error"，把错误写进 messages 后返回，不崩
       ├─ steps += 1，追加 assistant 消息（含 OpenAI 风格 tool_calls）
       ├─ 有 content → 记为 output（最后一次非空回答即最终答案）
       ├─ 无 tool_calls → STOP，status="completed"
       └─ 本轮所有 tool_call：走完五道关后按原顺序回灌
     └─ 循环耗尽 → status="max_steps"
```

停止条件是三种 `RunStatus`：`completed` / `max_steps` / `model_error`。

### 4.3 一次工具调用的五道关（顺序不可换）

```
tool_call → ①认名字 → ②验参数 → ③问权限 → ④真执行 → ⑤回结果
              ↓          ↓          ↓          ↓
           未知工具    参数非法    被拒绝     抛异常
              └──────────┴──────────┴──────────┘
                    全部转成错误文本回灌给模型
```

- **先校验再授权**：语法错误是模型的问题，权限才是人的问题，不该拿非法参数去打扰操作者。
- **错误一律回灌、绝不抛出**：模型第二次往往就改对了，崩溃等于白扔一整轮上下文。
- 同一轮多个 `tool_call` 各自独立，一个失败不影响其他。

### 4.4 工具系统

工具是声明式的：`name + description + input_schema + handler + consequential`。`description` 是写给模型看的提示词，`input_schema` 写严（`required` + `additionalProperties: False` + 数值/长度约束），把校验压力前移到 schema。新增工具只加一个 `Tool(...)` 并 `register()`，Loop 不感知具体工具。

### 4.5 权限与工作区隔离

- **隔离**：`_sandbox()` 统一入口——`expanduser` → `resolve()`（同时折叠 `..` 并跟随符号链接）→ 判断是否等于 root 或落在 root 之下。指向工作区外的符号链接解析后落在外部，同样被拒。
- **权限**：默认策略 `ConsequentialPolicy(interactive=AskUserPolicy())`——只读工具自动放行，`consequential=True`（如 `write_file`）才询问操作者，支持 `y / n / a`（`a` 记住本次会话）。`AskUserPolicy` 的读写通过注入可调用对象完成，测试可替换。

### 4.6 真实模型接入

`OpenAICompatibleModel` 用标准库 `urllib` 调用 Chat Completions，内部工具 spec 转成 `{"type":"function","function":{name,description,parameters}}`；返回的 `tool_calls` 反序列化回 `ToolCall`。消息历史本就是 OpenAI 风格，转换成本极低。换厂商只需换 `--base-url`/`--model-name`，或另写一个 `ModelClient`。

### 4.7 可观测性

`tracer` 是可选回调，不污染主循环。事件序列：`user_message → tool_call → validation_error / permission_denied / tool_error / tool_result → model_reply → finish`，每个事件带 `step`。`RunResult.messages` 本身就是完整可回放的轨迹。

## 五、运行示例（真实日志）

### 示例 1：完整任务——读资料、搜索、生成报告（写文件时 `y` 确认）

```console
$ "y" | python -m mini_agent.cli "阅读 workspace/materials 的资料，找出与 Agent Loop 有关的内容并生成一份总结。" \
    --root workspace --model-backend script --script examples/demo_replies.json

[step 0] [you] 阅读 workspace/materials 的资料，找出与 Agent Loop 有关的内容并生成一份总结。

[step 1] [model] 先看看 materials 目录里有什么。
[step 1] [call] list_dir(path='.')
[step 1] [result] list_dir -> d materials
[step 2] [model] 搜索与 Agent Loop 有关的内容。
[step 2] [call] search_text(pattern='Agent Loop')
[step 2] [result] search_text -> materials/intro.txt:1: Agent Loop 会让模型、工具与工具结果形成闭环。
[step 3] [model] 读取正文确认细节。
[step 3] [call] read_file(path='materials/intro.txt')
[step 3] [result] read_file -> Agent Loop 会让模型、工具与工具结果形成闭环。\nHarness 负责执行、权限和生命周期，模型负责选择下一步行动。\n
[step 4] [model] 把总结写入报告。
[step 4] [call] write_file(path='report.md', content='# Agent Loop 总结\n\n- ...')
允许执行工具 write_file(path='report.md', ...) [consequential] Write or append text to a file inside the workspace.? [y/N/a]: [step 4] [result] write_file -> wrote report.md (83 chars)
[step 5] [model] 已根据 materials/intro.txt 生成 workspace/report.md。
[step 5] [done] status=completed steps=5 :: 已根据 materials/intro.txt 生成 workspace/report.md。

status=completed steps=5
已根据 materials/intro.txt 生成 workspace/report.md。
```

### 示例 2：错误自愈——参数漏了，模型看到错误后自己改对

```console
$ python -m mini_agent.cli "读一下 materials。" --root workspace \
    --model-backend script --script examples/retry_replies.json --trust-all

[step 0] [you] 读一下 materials。
[step 1] [call] read_file()
[step 1] [invalid] read_file -> Error: invalid arguments: $.path: is required
[step 2] [model] 参数漏了，这次补上 path。
[step 2] [call] read_file(path='materials/intro.txt')
[step 2] [result] read_file -> Agent Loop 会让模型、工具与工具结果形成闭环。\nHarness 负责执行、权限和生命周期，模型负责选择下一步行动。\n
[step 3] [model] 材料共两行：说明 Agent Loop 与 Harness 的分工。
[step 3] [done] status=completed steps=3 :: 材料共两行：说明 Agent Loop 与 Harness 的分工。

status=completed steps=3
材料共两行：说明 Agent Loop 与 Harness 的分工。
```

### 示例 3：权限拒绝——操作者输入 `n`，拒绝后模型继续给出答复

```console
$ "n" | python -m mini_agent.cli "把总结写进 workspace/report.md。" --root workspace \
    --model-backend script --script examples/demo_replies.json

[step 4] [call] write_file(path='report.md', content='# Agent Loop 总结\n\n- ...')
允许执行工具 write_file(path='report.md', ...) [consequential] ... [y/N/a]: [step 4] [denied] write_file -> Error: permission denied for 'write_file': rejected by the operator
```

### 示例 4：越界访问被挡（单元测试同款场景）

```
[call] read_file(path='../outside.txt')
[result] read_file -> Error: 'read_file' failed: path escapes the workspace: ../outside.txt
```

## 六、已完成的拓展任务（4 / 8）

1. **并行执行互不依赖的只读工具**：同一轮回复里的多个只读调用用 `ThreadPoolExecutor` 并发执行，结果仍按模型请求顺序回灌（`_handle_calls`）；`consequential=True` 的写操作保持串行，避免并发写状态。
2. **处理过长的工具输出**：`max_tool_chars`（默认 8000）截断单次工具结果并附 `[truncated N chars]`，避免一个工具把上下文撑爆。
3. **处理过长的对话上下文**：`max_context_chars`（默认 20000）。每轮调用模型前估算历史体积，超出就把较早消息的正文压成 `前 80 字 + [compacted N chars]`。**只缩短正文、不删消息**——删掉 tool 消息会破坏 `tool_calls` / `tool_call_id` 的配对，下一次请求会被厂商拒绝；system 与最近 `compact_keep_recent`（默认 6）轮始终原样保留。
4. **保存并恢复会话**：`session.py` 的 `SessionStore` 把消息历史按会话 ID 存成 JSON，CLI `--session demo` 存在即续接、结束即存回；`--list-sessions` 可列出。Agent 本身仍是无状态的——`run(query, history=...)` 由调用方决定是否喂回历史，测试因此不需要碰文件系统。

演示续接：

```console
$ python -m mini_agent.cli "阅读 materials 的资料并生成总结。" --root workspace \
    --model-backend script --script examples/demo_replies.json --session demo
session saved: .sessions/demo.json (11 messages)

$ python -m mini_agent.cli "继续，把结论再压缩成一句话。" --root workspace \
    --model-backend script --script examples/retry_replies.json --session demo --trust-all
resumed session demo (11 messages)
session saved: .sessions/demo.json (17 messages)
```

其余方向（Todo/Planning、Hooks、MCP、Web/TUI、任务集评估）未实现，见第八节。

## 七、目录结构

```
.
├── docs/DEFENSE.md   # 答辩与面试口径
├── src/mini_agent/
│   ├── agent.py         # Agent Loop
│   ├── contracts.py     # 题目提供的基础接口（未改动）
│   ├── fake_model.py    # 题目提供的离线模型
│   ├── validation.py    # JSON Schema 子集校验
│   ├── permissions.py   # 权限策略
│   ├── models.py        # OpenAI 兼容客户端 + 脚本回放
│   ├── context.py       # 上下文压缩（只缩短正文，不删消息）
│   ├── session.py       # 会话持久化（保存 / 恢复 / 列出）
│   ├── trace.py         # 轨迹事件与渲染
│   ├── cli.py           # CLI 入口
│   └── tools/           # ToolRegistry + filesystem / search / system
├── tests/               # 公开测试 + 边界用例
├── examples/            # 离线回放脚本（生成上面的日志）
└── workspace/           # Agent 的工作区，工具不得越界
```

## 八、当前已知问题

1. **符号链接用例在部分 Windows 上被跳过**：创建符号链接需要管理员或开发者模式，`test_symlink_escape_is_rejected` 在无权创建时 `skip`；隔离逻辑本身依赖 `resolve()`，Linux/macOS 上可正常运行断言。
2. **并行只对只读工具生效**：写操作刻意串行，牺牲吞吐换可预期的状态变更。
3. **上下文压缩是「尽力而为」**：system 与最近若干轮受保护，因此单次超大工具结果仍可能让总量高于预算（需要与 `max_tool_chars` 配合形成硬上限）；压缩只做截断式摘要，不是语义摘要。
4. **真实模型能力有限**：只支持 OpenAI 兼容的 Chat Completions，未做流式输出、重试退避、token 预算与费用统计。
5. **计算器只支持算术子集**：基于白名单 AST 求值，不支持变量与科学函数。
6. **会话持久化只存消息历史**：不存工具注册表快照与权限决策，恢复后工具集以当前代码为准；会话文件不做加密，含敏感内容时需自行处理。
7. **路径隔离只覆盖文件系统工具**：目前工具集中不存在 shell / 网络类工具，因此不存在绕过隔离的旁路；若后续加入此类工具，需要重新设计沙箱边界。

## 九、验收时的常见问题速查

- **现场接一个新工具怎么做？** 写一个 `Tool(name, description, input_schema, handler)` 传给 `Agent`，或在 `tools/` 下加一个 `build_xxx_tools()` 并在 `build_default_tools()` 里注册；Loop 代码零改动。
- **工具失败会怎样？** 不会崩。错误以 `Error: ...` 的形式作为工具结果回灌，模型下一轮自行调整。
- **怎么证明闭环是对的？** `tests/` 里的用例断言消息历史的形状（assistant 的 `tool_calls`、tool 消息的 `tool_call_id` 对应关系），配合 `FakeModel` 让模型行为完全确定。
- **调试靠什么？** `--show-messages` 打印最终消息历史，或注入 `tracer` 拿到结构化事件流。
