# 答辩/面试指南：Mini Agent Harness

README 讲**做了什么**，这份讲**为什么这么做、被追问怎么答**。括号里是代码位置，照着指就行。

---

## 零、30 秒电梯演讲（开场背这段）

> 我做的是 Agent Harness，不是 Agent 应用。核心一句话：**模型负责选下一步，程序负责把这一步安全地做完**。
> 循环就是：发工具清单 → 模型回答或调用工具 → 校验参数、问权限、执行、把结果回灌 → 再交给模型。
> 前提是「模型不可信」：参数乱填、路径越界、工具不存在、输出超长，都不崩，而是变成一句模型能读懂的错误回灌回去让它自己改。
> 没用任何 Agent 框架，Loop 全手写，零第三方运行时依赖。

---

## 一、为什么这么分层

| 决策 | 选择 | 理由（一句） |
| --- | --- | --- |
| 模型接入 | 只依赖 `ModelClient` 协议（`contracts.py`） | Agent 不该知道模型是哪家的；换厂商只换一个对象，Loop 一行不改，`FakeModel` 就是这么塞进来的 |
| 权限 | 策略注入（`PermissionPolicy`） | 工具只声明有没有副作用（`consequential`），让不让执行由外部决定——测试里永远不需要 `input()` |
| 校验 | 独立 `validation.py`，不依赖 `jsonschema` | 只实现 schema 真用到的子集，不认识的关键字忽略而非报错，零依赖 |
| 可观测性 | `tracer` 回调 | 主循环不该为打印负责；`RunResult.messages` 本身就是可回放的轨迹 |
| 工具 | 声明式 `Tool` + 注册表 | 新增工具 = 一个 dataclass + 一次 `register()`，Loop 零改动 |

一句话总结：**模型只选，Harness 只做，策略外挂，工具声明式。**

---

## 二、关键代码走读（被要求「指给我看」时用）

| 想证明的点 | 看这里 |
| --- | --- |
| 主循环与三种停止条件 | `agent.py:83` `run()`：`completed`（无 tool_calls）/ `max_steps`（耗尽）/ `model_error`（`complete` 抛异常） |
| 一次工具调用的五道关 | `agent.py:162` `_plan()` 认名字→验参数→问权限；`agent.py:198` `_execute_plans()` 执行并回灌 |
| 错误一律回灌不抛出 | `agent.py:178/184/191/232/234` —— 未知工具、参数非法、权限拒绝、`TypeError`、任意异常，全是 `Error: ...` 字符串 |
| 只读并行、写串行 | `agent.py:204` 只读走 `ThreadPoolExecutor`；`agent.py:215` 副作用按请求顺序串行 |
| 超长输出截断 | `agent.py:237` `_clip()`，默认 `max_tool_chars=8000`，附 `[truncated N chars]` |
| 上下文压缩 | `context.py` `compact_messages()`，每轮调模型前由 `agent.py` `_compact()` 触发 |
| 会话持久化 | `session.py` `SessionStore` + `run(query, history=...)` |
| 沙箱隔离 | `tools/filesystem.py` `_sandbox()`：`expanduser` → `resolve()` → 判断是否落在 root 之下 |
| 权限询问 | `permissions.py:86` `AskUserPolicy`，`y/n/a` 三态，读写用注入的 callable |
| 参数校验 | `validation.py:63` `validate_instance()`，最多返回 8 条错误避免刷屏 |

---

## 三、高频追问 Q&A

### A. Agent Loop

**Q：模型一直调工具不收敛？**
A：`max_steps`（默认 8）是硬预算，耗尽就交出已有进展；软的一层是每轮工具结果都进历史，模型看得见自己做过什么。没做重复调用检测，是可改进点。

**Q：为什么默认不加 system prompt？**
A：公开测试断言 `messages[0]` 是用户消息。`system_prompt` 是可选参数，传了才插在最前——默认保守，能力不丢。

**Q：为什么用 OpenAI 风格消息（`role: tool` + `tool_call_id`）？**
A：事实标准，接真实模型几乎零转换；代价是被这个形状绑住，换 Claude 风格需要一层适配（未做）。

**Q：一轮里多个 tool_call，一个失败会拖垮其他吗？**
A：不会。每个 call 独立走五道关，失败的只把那一条换成错误文本，其余照常执行、按原顺序回灌。

### B. 安全（最容易被追着问）

**Q：怎么防止读到工作区外？**
A：所有路径工具统一走 `_sandbox()`：`expanduser` → `resolve()`（折叠 `..`、跟随符号链接）→ 判断是否落在 root 之下。`../outside.txt`、绝对路径越界、指向外部的符号链接在同一处被拒。

**Q：为什么不直接字符串匹配拦 `..`？**
A：黑名单拦不全（绝对路径、编码、大小写、`a/../b/../..`）。`resolve()` 后做前缀判断，是把「看起来像」变成「实际是」。

**Q：为什么先校验参数再问权限？**
A：语法错误是模型的问题，权限才是人的问题。拿非法参数去打扰操作者没意义。顺序：认名字 → 验参数 → 问权限 → 执行。

**Q：权限被拒之后呢？**
A：拒绝不是终止，是一条工具结果 `Error: permission denied...`，模型可以换个说法或告诉用户「你拒绝了」。**拒绝必须可恢复**，否则一次手误废掉整轮上下文。

**Q：计算器怎么保证安全？**
A：不用 `eval`。`ast.parse(mode="eval")` 后白名单递归：常量、算术节点 + `abs/min/max/round/pow/sum/len`（`tools/system.py:12`）；变量、白名单外函数、属性访问、下标一律拒绝。

### C. 工程性

**Q：怎么测试模型驱动的循环？**
A：换成 `FakeModel` 按脚本回放，断言的是**消息历史的形状**（`tool_calls` 与 `tool_call_id` 一一对应），而不是模型说得好不好。`35 passed / 1 skipped`，题目点名的六类异常每类都有用例。

**Q：那条 skip 是什么？**
A：符号链接越界用例。Windows 无权创建符号链接时跳过——**主动跳过比假装通过诚实**。

**Q：为什么不用框架？**
A：题目要求手写，且框架会把最想展示的部分（五道关、停止条件、回灌）藏起来。代价：流式、重试退避、token 计费没做。

**Q：上下文快撑爆怎么办？**
A：两层。单次结果硬截断（`max_tool_chars`）+ 每轮前 `compact_messages()` 把较早正文压成「前 80 字 + `[compacted N chars]`」。**只缩短正文、绝不删消息**——tool 消息必须和请求它的 `tool_calls` 配对，删一条下次请求就被拒。system 与最近 6 轮受保护。

**Q：会话怎么跨进程继续？Agent 为什么无状态？**
A：`SessionStore` 按会话 ID 存 JSON，`--session demo` 存在即续接、结束即存回；历史由调用方经 `run(query, history=...)` 喂回，**Agent 不碰文件系统**，测试无需真实磁盘。会话 ID 过白名单正则挡路径穿越，文件损坏抛 `SessionError`。

**Q：并行会状态不一致吗？**
A：只对 `consequential=False` 的只读工具并行，写操作刻意串行且保序——**正确性优先于速度**。

### D. 现场加工具（大概率考）

**Q：加一个「发 HTTP 请求」的工具怎么做？**
A：三步，Loop 零改动：① 写 `Tool(name="http_get", description=..., input_schema={"type":"object","properties":{"url":{"type":"string"}},"required":["url"],"additionalProperties":False}, handler=http_get, consequential=False)`；② `registry.register(tool)`；③ schema 里用 `pattern` 限协议、`maxLength` 限长度，**把校验压力前移**。
风险两点：网络是沙箱的旁路，需显式域名白名单；必须设超时，否则挂住整个 Loop。

---

## 四、主动暴露的不足（诚实是加分项）

1. 符号链接用例在部分 Windows 上跳过 → 换 Linux/macOS 跑 CI。
2. 写操作串行 → 可按资源（路径）加锁来安全并行。
3. 压缩是截断式、尽力而为 → 更进一步用模型生成语义摘要，代价是额外 token 与延迟。
4. 真实模型只支持 OpenAI 兼容 Chat Completions，无流式、重试退避、token 计费。
5. 计算器只支持算术子集，无变量与科学函数。
6. 会话只存消息历史（不存工具集快照与权限决策），文件不加密。
7. 路径隔离只覆盖文件系统工具；加 shell/网络工具后沙箱边界要重新设计。

被问「下一步做什么」：**Todo / Planning 能力**——加 `todo_write` 状态工具 + 把待办插进每轮上下文；它和压缩天然配合，待办属于不该被压掉的一类。

---

## 五、现场演示脚本（照着敲）

```bash
# 1) 测试：确定性地证明闭环正确
python -m pytest -q

# 2) 完整任务：读资料 → 搜索 → 写报告（写文件时输 y，演示权限关卡）
PYTHONPATH=src python -m mini_agent.cli \
  "阅读 workspace/materials 的资料，找出与 Agent Loop 有关的内容并生成一份总结。" \
  --root workspace --model-backend script --script examples/demo_replies.json

# 3) 错误自愈：参数漏了，模型看到错误后自己改对
PYTHONPATH=src python -m mini_agent.cli "读一下 materials。" --root workspace \
  --model-backend script --script examples/retry_replies.json --trust-all

# 4) 会话持久化：第二次带 --session 会先续接（打印 resumed ...），再存回
PYTHONPATH=src python -m mini_agent.cli "继续，把结论再压缩成一句话。" --root workspace \
  --model-backend script --script examples/retry_replies.json --session demo --trust-all
```

演示时口头点出的三个瞬间：`[invalid] ... $.path: is required` → 下一轮模型自己补参数（**错误回灌**）；`允许执行工具 write_file(...) ? [y/N/a]` → 只有写操作才问人（**最小惊讶**）；`Error: 'read_file' failed: path escapes the workspace` → 越界被挡但程序没崩（**沙箱生效**）。

---

## 六、术语速查

- **Harness**：包在模型外面的程序，管工具执行、权限、生命周期。模型在里，Harness 在外。
- **Agent Loop**：模型输出 → 执行工具 → 结果回灌 → 再问模型，直到停止。
- **Tool calling**：模型不直接调函数，而是输出 JSON 说「我想调 X、参数是 Y」，由程序执行。
- **Consequential**：会改变外部状态的工具（写文件、发请求），需人工确认；相对的是只读。
- **Stop condition**：`completed` / `max_steps` / `model_error` 三种。
