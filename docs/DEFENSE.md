# 答辩/面试指南：Mini Agent Harness

面向验收与面试的一份「怎么说」文档。README 讲**做了什么**，这份讲**为什么这么做、被追问怎么答**。
所有结论都能在代码里找到对应位置，括号里是文件与关键行。

---

## 零、30 秒电梯演讲（开场先背这段）

> 我做的是一个 Agent Harness，不是 Agent 应用。核心是一句话：**模型负责选下一步，程序负责把这一步安全地做完**。
> 整个项目就一个循环：把工具清单交给模型 → 模型回答或调用工具 → 我这边校验参数、问权限、真正执行、把结果回灌 → 再交给模型。
> 设计时我把「模型不可信」当成前提：参数乱填、路径越界、工具不存在、输出超长，都不要崩，而是变成一句模型能读懂的错误回灌回去，让它自己改。
> 没有用任何 Agent 框架，Loop 全部手写，零第三方运行时依赖。

说完这三句，面试官大概率会顺着「不可信」往下问，后面的 Q&A 就是为这个准备的。

---

## 一、为什么这么分层

| 决策 | 选择 | 理由（这么说） |
| --- | --- | --- |
| 模型接入 | 只依赖 `ModelClient` 协议（`contracts.py`） | "Agent 不该知道模型是哪家的。换厂商只需要换一个对象，Loop 一行不改。"离线 `FakeModel` 就是靠这个塞进去的，测试因此完全确定、不需要 API Key。 |
| 权限 | 策略注入（`PermissionPolicy`），不是写死在工具里 | "工具只声明自己有没有副作用（`consequential`），到底让不让执行由外部策略决定。测试里永远不需要 `input()`。"对比"把 `if dangerous: input()` 写在工具里"的做法，那个无法测试、无法批量放行。 |
| 校验 | 独立的 `validation.py`，不依赖 `jsonschema` | "题目允许零依赖我就零依赖。只实现工具 schema 真用到的子集（type/required/enum/长度/数值/pattern/additionalProperties），不认识的关键字忽略而不是报错，schema 写复杂了也不会卡住调用。" |
| 可观测性 | `tracer` 回调，不进主循环逻辑 | "主循环不该为打印负责。`RunResult.messages` 本身就是可回放的轨迹，tracer 只是给人看的一层皮。" |
| 工具 | 声明式 `Tool(name, description, input_schema, handler)` + 注册表 | "新增工具 = 一个 dataclass + 一次 `register()`，Loop 代码零改动。"这是现场加工具题的标准答案。 |

一句话总结分层：**模型只选，Harness 只做，策略外挂，工具声明式。**

---

## 二、关键代码走读（被要求"指给我看"时用）

| 想证明的点 | 看这里 |
| --- | --- |
| Agent Loop 主循环、三种停止条件 | `src/mini_agent/agent.py:83` `run()`；`while steps < self.max_steps` → `completed`（无 tool_calls）/ `max_steps`（循环耗尽）/ `model_error`（`complete` 抛异常） |
| 一次工具调用的五道关 | `agent.py:162` `_plan()`：认名字 → 验参数 → 问权限；`agent.py:198` `_execute_plans()`：真执行；`agent.py:160`：回结果 |
| 错误不抛出、一律回灌 | `agent.py:178/184/191/232/234` —— 未知工具、参数非法、权限拒绝、`TypeError`、任意异常，五种失败全是 `Error: ...` 字符串 |
| 只读并行、写操作串行 | `agent.py:204` `if self.parallel_tools and len(read_only) > 1` → `ThreadPoolExecutor`；`agent.py:215` 副作用调用保持顺序串行 |
| 超长输出截断 | `agent.py:237` `_clip()`，默认 `max_tool_chars=8000`，附带 `[truncated N chars]` 让模型知道被截了 |
| 上下文压缩 | `context.py` `compact_messages()`，每轮调用模型前由 `agent.py` `_compact()` 触发，默认 `max_context_chars=20000` |
| 会话持久化 | `session.py` `SessionStore`；`run(query, history=...)` 由调用方决定是否喂回历史 |
| 沙箱隔离 | `tools/filesystem.py` 的 `_sandbox()`：`expanduser` → `resolve()` → 判断是否等于 root 或在 root 之下 |
| 权限询问 | `permissions.py:86` `AskUserPolicy`，`y/n/a` 三态，`a` 记住本次会话；读写用注入的 callable，测试可替换 |
| 参数校验 | `validation.py:63` `validate_instance()`，最多返回 8 条错误（`_MAX_ERRORS`）避免刷屏 |

---

## 三、高频追问 Q&A

### A. 关于 Agent Loop

**Q：模型一直调工具不停下来怎么办？**
A：两层保险。一是 `max_steps` 预算（默认 8，CLI `--max-steps` 可调），耗尽就 `status="max_steps"` 并把已有进展交出去；二是每一轮的工具结果都会进消息历史，模型看得见自己做过什么，正常的模型会收敛。我只做硬性预算，没做"重复调用检测"，这是可改进点。

**Q：为什么默认不加 system prompt？**
A：公开测试断言 `messages[0]` 就是用户消息。`Agent.__init__` 里 `system_prompt` 是可选参数，传了才会插在最前面——默认行为保守，能力不丢。

**Q：消息历史为什么用 OpenAI 风格（`role: tool` + `tool_call_id`）？**
A：因为它是事实标准。好处是接真实模型时几乎零转换（`models.py` 里 spec 转 `{"type":"function",...}`，`tool_calls` 反过来转），成本极低；代价是我被这个形状绑住了，换 Claude 风格需要一层适配——目前没做。

**Q：为什么每轮都把工具清单重新发给模型？**
A：工具集在会话内是稳定的，重发是为了让每轮请求自包含、可单独重放，也便于将来做动态工具集（按权限裁掉不可用的工具）。代价是重复的 token，长会话下可以优化成只在首轮发、后续增量。

**Q：一轮里多个 tool_call，一个失败会影响其他吗？**
A：不会。每个 call 独立走五道关（`_plan` 逐个生成 plan），失败的只把那一条替换成错误文本，其余照常执行、按原顺序回灌。

### B. 关于安全（这组最容易被追着问）

**Q：怎么防止 Agent 读到工作区外的文件？**
A：所有路径工具统一走 `_sandbox()`：`expanduser` 展开 `~` → `resolve()` 把 `..` 折叠掉并跟随符号链接 → 再判断是否等于 root 或在 root 之下。所以 `../outside.txt`、绝对路径越界、**指向外部的符号链接**（解析后落在外部）都会在同一处被拒。

**Q：为什么不直接用字符串匹配拦 `..`？**
A：黑名单拦不全。绝对路径、URL 编码、符号链接、大小写、`a/../b/../..` 都能绕过字符串检查。`resolve()` 之后做一次前缀判断，是把"看起来像"变成"实际是"——这是这类问题的正确解法。

**Q：为什么先校验参数再问权限？**
A：语法错误是模型的问题，权限才是人的问题。拿一份非法参数去打扰操作者，既没意义又消耗人的注意力。顺序是：认名字 → 验参数 → 问权限 → 执行。

**Q：权限被拒之后呢？**
A：拒绝不是终止，是一条工具结果：`Error: permission denied for 'write_file': rejected by the operator`。模型下一轮可以选择换个说法、换个路径，或者直接告诉用户"你拒绝了，我没写"。**拒绝必须可恢复**，否则一次手误就废掉整轮上下文。

**Q：计算器怎么保证安全？**
A：不用 `eval`。`ast.parse(mode="eval")` 后走白名单递归：只放行常量、一元/二元算术节点，以及白名单里的函数（`abs/min/max/round/pow/sum/len`，见 `tools/system.py:12`）。变量（`ast.Name`）直接拒绝，白名单外的函数、关键字参数、属性访问、下标一律拒绝。不支持变量与科学函数——这是明确的取舍，README 第八节里写了。

### C. 关于工程性

**Q：怎么测试一个"模型"驱动的循环？**
A：把模型换成 `FakeModel`：它按脚本回放回复，`Agent` 只依赖 `ModelClient` 协议。于是每一步都确定，测试断言的是**消息历史的形状**——assistant 的 `tool_calls`、tool 消息的 `tool_call_id` 一一对应，而不是断言模型"说得好不好"。`$ python -m pytest -q` → 35 passed / 1 skipped。题目点名的六类异常——不存在的工具、错误参数、工具执行失败、权限拒绝、步数超限、模型请求异常——每一类都有对应用例（`tests/test_agent_behaviour.py`、`tests/test_tools.py`）。

**Q：那条 skip 的用例是什么？**
A：符号链接越界测试。Windows 上创建符号链接需要管理员或开发者模式，无权创建时 `pytest.skip`。隔离逻辑本身依赖 `resolve()`，Linux/macOS 上能正常断言——**主动跳过比假装通过诚实**。

**Q：没用框架是为什么？**
A：题目就是要求手写 Loop，而且这个规模下框架是负担：LangChain 之类的抽象会把我最想展示的部分（五道关、停止条件、回灌）藏起来。代价是流式输出、重试退避、token 计费这些我没做，真上生产会补。

**Q：对话越来越长、快撑爆上下文怎么办？**
A：两层。一是单次工具结果硬截断（`max_tool_chars`）；二是每轮调模型前 `compact_messages()` 估算历史体积，超预算就把较早消息的正文压成「前 80 字 + `[compacted N chars]`」。**只缩短正文、绝不删消息**——OpenAI 风格要求 tool 消息必须和请求它的 `tool_calls` 配对，删掉任何一条，下一次请求会被厂商直接拒绝。system 和最近 6 轮始终原样保留。它是尽力而为：一个超大工具结果仍可能让总量高于预算，所以两层必须同时存在。

**Q：会话怎么跨进程继续？为什么要设计成 Agent 无状态？**
A：`SessionStore` 把消息历史按会话 ID 存成 JSON，`--session demo` 存在即续接、结束即存回。历史由调用方通过 `run(query, history=...)` 喂回，**Agent 自己不碰文件系统**——这样测试完全不需要真实磁盘，也能随时换成数据库/Redis。两个安全细节：会话 ID 过白名单正则，`../escape`、`a/b` 直接拒绝（路径穿越）；文件损坏抛 `SessionError`，由 CLI 转成错误码而不是崩在解析处。

**Q：并行执行会不会导致状态不一致？**
A：只对 `consequential=False` 的只读工具并行，`consequential=True` 的写操作刻意保持串行且按模型请求顺序执行。牺牲吞吐换可预期的状态变更——**正确性优先于速度**。

### D. 现场加工具（大概率会考）

**Q：现在让你加一个"发 HTTP 请求"的工具，怎么做？**
A：三步，Loop 零改动：

1. 写 `Tool(name="http_get", description="...", input_schema={"type":"object","properties":{"url":{"type":"string"}},"required":["url"],"additionalProperties":False}, handler=http_get, consequential=False)`；
2. `registry.register(tool)`，或在 `tools/` 下加 `build_http_tools()` 并在 `build_default_tools()` 里注册；
3. schema 里用 `pattern` 限制协议、用 `maxLength` 限制长度——**把校验压力前移到 schema**。

加完要说明的两点风险：它会成为沙箱的旁路（网络不受文件隔离约束），所以要显式域名白名单；以及超时必须设置，否则会挂住整个 Loop。

---

## 四、主动暴露的不足（诚实是加分项）

照 README 第八节说，重点是**每条都知道怎么改**：

1. 符号链接用例在部分 Windows 上跳过 → 换 Linux/macOS 或用 `pytest.mark.skipif` 之外的方案跑 CI。
2. 写操作串行 → 可以引入按资源（路径）加锁来安全并行。
3. 上下文压缩是"尽力而为"且是截断式摘要：system 与最近若干轮受保护，单次超大工具结果仍可能超预算；更进一步的做法是调用模型生成语义摘要，代价是额外的 token 与延迟。
4. 真实模型只支持 OpenAI 兼容的 Chat Completions，无流式、无重试退避、无 token 预算与费用统计 → 生产补齐。
5. 计算器只支持算术子集（AST 白名单，仅 `abs/min/max/round/pow/sum/len`），不支持变量与科学函数。
6. 会话只存消息历史，不存工具集快照与权限决策；会话文件不加密 → 恢复后工具集以当前代码为准，含敏感内容需额外处理。
7. 路径隔离只覆盖文件系统工具；一旦加入 shell/网络工具，沙箱边界要重新设计（见上一节 HTTP 工具）。

被问"如果继续做，你下一个做什么"：**Todo / Planning 能力**。多步任务里模型最容易"忘了还要做什么"，做法是加一个 `todo_write` 只读状态工具 + 把当前待办清单插进每轮上下文；它和已有的压缩天然配合——待办属于必须保留的那一类，不该被压掉。

---

## 五、现场演示脚本（30 秒，照着敲）

```bash
# 1) 测试：确定性地证明闭环正确
python -m pytest -q

# 2) 完整任务：读资料 → 搜索 → 写报告（写文件时输入 y 确认，演示权限关卡）
PYTHONPATH=src python -m mini_agent.cli \
  "阅读 workspace/materials 的资料，找出与 Agent Loop 有关的内容并生成一份总结。" \
  --root workspace --model-backend script --script examples/demo_replies.json

# 3) 错误自愈：参数漏了，模型看到错误后自己改对
PYTHONPATH=src python -m mini_agent.cli "读一下 materials。" --root workspace \
  --model-backend script --script examples/retry_replies.json --trust-all

# 4) 会话持久化：第二次带上 --session 会先续接（打印 resumed ...），再存回
PYTHONPATH=src python -m mini_agent.cli "继续，把结论再压缩成一句话。" --root workspace \
  --model-backend script --script examples/retry_replies.json --session demo --trust-all
```

演示时值得口头点出来的三个瞬间：

- `[invalid] ... $.path: is required` → 下一轮模型自己补上了参数（**错误回灌的价值**）
- `允许执行工具 write_file(...) ? [y/N/a]` → 只有写操作才问人，只读直接跑（**权限的最小惊讶**）
- `Error: 'read_file' failed: path escapes the workspace` → 越界被挡住，程序没崩（**沙箱生效**）

---

## 六、术语速查（别在这些词上卡住）

- **Harness**：包在模型外面的那层程序，负责工具执行、权限、生命周期。模型在里，Harness 在外。
- **Agent Loop**：模型输出 → 执行工具 → 结果回灌 → 再问模型，直到停止。
- **Tool calling / function calling**：模型不直接调函数，而是输出一段 JSON 说"我想调 X、参数是 Y"，由程序去执行。
- **Consequential**：会改变外部状态的工具（写文件、发请求），需要人工确认；与之相对的是只读。
- **Stop condition**：`completed` / `max_steps` / `model_error` 三种。
