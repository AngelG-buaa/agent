# myAgent — 最小化 LLM Agent 系统

实习项目。希望通过这个项目找到大厂的有含金量的 Agent 实习岗位。

## 项目背景

这是 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 教程的实践项目，通过阅读 Claude Code 源码理解 Agent 架构，然后从零实现一个简化版。项目经历了两个主要迭代：

| 迭代 | Spec | 内容 |
|------|------|------|
| 001 | [specs/001-todo-write-tool/](specs/001-todo-write-tool/) | 加入 TodoWrite 工具——Agent 在执行复杂任务前规划步骤、跟踪进度 |
| 002 | [specs/002-task-subagent-tool/](specs/002-task-subagent-tool/) | 加入 Task 工具 + SubAgent 子类——上下文隔离、子任务委派 |
| 003 | [specs/003-context-compact/](specs/003-context-compact/) | Context Compact——四层渐进式压缩管线，防止长对话上下文溢出 |

每个迭代遵循 [Spec Kit](.specify/) 流程：Specify → Clarify → Plan → Tasks → Implement → Analyze。

## 目录结构

```
Stage 2/project/
├── main.py                  # CLI 入口：组装 LLM → Executor → Agent，执行问答
├── config.py                # 集中配置（LLM API、Embedding、RAG、Workdir）
├── hooks.py                 # Hook 事件系统（register_hook / trigger_hooks）
├── README.md                # ← 你正在看的这个文件
│
├── agent/                   # Agent 层：核心循环 + 提示词 + 工具函数 + 压缩管线
│   ├── agent.py             # Agent 类 (Think→Act→Observe) + SubAgent(Agent) 子类
│   ├── compact.py           # 四层上下文压缩管线 (L3→L1→L2→L4)
│   ├── llm_client.py        # LLMClient：OpenAI 兼容 API 封装
│   ├── prompts.py           # SYSTEM_PROMPT + SUB_SYSTEM_PROMPT
│   └── utils.py             # 打印回调 + filter_assistant_message + 消息访问器
│
├── tools/                   # 工具层：12 个内置工具
│   ├── __init__.py          # register_all() — 统一注册入口
│   ├── task.py              # TaskTool + spawn_subagent() — 子任务委派
│   ├── todo_write.py        # TodoWriteTool + 提醒 hooks — 任务规划
│   ├── bash.py              # Bash 命令执行
│   ├── read_file.py         # 读取文件
│   ├── write_file.py        # 写入文件
│   ├── edit_file.py         # 编辑文件
│   ├── read_chunk.py        # 按 ID 读取 RAG 文档块
│   ├── calculator.py        # 数学计算
│   ├── get_time.py          # 获取当前时间
│   ├── web_search.py        # Web 搜索
│   ├── web_fetch.py         # Web 页面抓取
│   └── search_knowledge.py  # RAG 知识库检索
│
├── tooling/                 # 工具基础设施层
│   ├── base.py              # Tool 抽象基类 + ToolParameter 数据类
│   ├── registry.py          # ToolRegistry：按名注册/查找/导出 schema
│   ├── executor.py          # ToolExecutor：实例级权限检查 + 工具分发 (engine/approver 构造注入)
│   └── permission/          # 权限子系统（实例级注入，非全局 Hook）
│       ├── engine.py        # PermissionEngine + PermissionGrant：评估管线 + grant/listener 接口
│       ├── policy.py        # RuleSource → 内置安全策略（19 条规则）
│       └── exceptions.py    # PermissionArchitectureError → 权限异常类型
│
├── rag/                     # RAG 子系统（离线 ingest + 在线检索）
│   ├── factory.py           # 单例工厂
│   ├── parser.py            # 文件解析（TXT/PDF/代码）
│   ├── chunker.py           # 句级分块 + token 预算合并
│   ├── embedder.py          # BGE-M3 向量编码
│   ├── vector_store_base.py # 向量存储抽象接口
│   ├── faiss_store.py       # FAISS 实现
│   ├── qdrant_store.py      # Qdrant 实现
│   ├── indexer.py           # 索引编排：parse→chunk→embed→store
│   ├── retriever.py         # 检索编排：encode→search→fetch chunks
│   ├── chunk_info.py        # ChunkInfo 数据模型
│   └── prompts.py           # 引用格式规则
│
├── tests/                   # 测试
│   ├── test_compact.py      # 上下文压缩管线（28 tests）
│   ├── test_todo_write.py   # TodoWrite 相关（34 tests）
│   └── test_task.py         # SubAgent + Task + Agent 扩展（37 tests）
│
├── docs/                    # 设计文档
│   ├── architecture-philosophy.md  # 架构原则（从 Claude Code 源码提炼）
│   ├── anti-patterns.md            # 开发中的反模式记录
│   └── TECH_DEBT.md                # 技术债追踪
│
├── specs/                   # 功能规范（Spec Kit 产物）
│   ├── 001-todo-write-tool/
│   ├── 002-task-subagent-tool/
│   └── 003-context-compact/
│
└── .specify/                # Spec Kit 配置
    ├── memory/constitution.md  # 项目宪章
    ├── templates/              # spec/plan/tasks 模板
    └── feature.json            # 当前活跃 feature 指针
```

## 核心概念

### Agent Loop（最重要）

```python
# agent/agent.py — Agent.run()
for _ in range(self.max_steps):
    # ① PreLLMCall hook（可注入消息）
    inject = trigger_hooks("PreLLMCall")

    # ② Compact: 四层渐进式上下文压缩
    compact_pipeline(messages, self.llm)

    # ③ Think: LLM 调用
    stop_reason, msg = self.llm.chat(messages, schemas)

    # ④ Act: 执行工具调用
    if stop_reason == "tool_calls":
        self._execute_tool_calls(msg.tool_calls, messages)

    # ⑤ PostRound hook（副作用跟踪）
    trigger_hooks("PostRound", stop_reason, tool_calls)

    # ⑥ Observe: 判断终止
    if stop_reason != "tool_calls":
        return msg.content
```

**核心原则（Constitution IX）**：循环体不随功能迭代增长。新能力通过构造参数（`tool_filter`、`print_handler`）或子类覆盖（`SubAgent._execute_tool_calls()`）注入。

### 工具系统

所有工具继承 `Tool` 基类（`tooling/base.py`）。三个核心方法：

| 方法 | 用途 |
|------|------|
| `get_parameters()` | 声明参数 schema |
| `run(params) → dict` | 执行工具，返回结果 |
| `to_schema()` | 导出 OpenAI function calling 格式 |

`ToolExecutor` 是 Agent 与工具之间的唯一网关。构造时注入 `PermissionEngine` + `Approver`，权限检查在实例内部完成（不经过全局 Hook）。执行管线：工具查找 → 实例级权限检查 → 全局 PreToolUse hooks（日志/观测） → 工具执行 → PostToolUse hooks。

### Hook 系统

7 个事件类型（`hooks.py`）。回调返回非 None 的 dict 即中断链路。注册方式：`register_hook(event, callback)`。

| 事件 | 触发位置 | 用途 |
|------|---------|------|
| `SessionStart` | main.py | 会话初始化 |
| `UserPromptSubmit` | Agent.run() | 用户输入后 |
| `PreLLMCall` | Agent.run() 每轮 | 消息注入（todo 提醒） |
| `PreToolUse` | executor.execute() | 工具执行前通知（日志/观测，非权限） |
| `PostToolUse` | executor.execute() | 工具执行后 |
| `PostRound` | Agent.run() 每轮 | 轮数跟踪 |
| `PreAgentStop` | Agent.run() 退出 | Agent 停止前 |

**注意**：`PreLLMCall` 是控制型 hook（返回值改变循环行为），其余为通知型。权限检查不再走全局 Hook——已改为 ToolExecutor 实例内部通过构造注入的 PermissionEngine 完成。参见 [TECH_DEBT #1](docs/TECH_DEBT.md)。

### Context Compact（上下文压缩）

`agent/compact.py`。每轮 LLM 调用前自动运行四层渐进式压缩，防止长对话上下文溢出。原则：便宜的先跑，贵的后跑。

| 层 | 函数 | 触发条件 | API 调用 | 做了什么 |
|----|------|---------|----------|---------|
| L3 | `tool_result_budget()` | 最近一轮 tool 消息总量 >500KB | 0 | 超大结果写入 `.task_outputs/tool-results/`，消息中只留 2000 字符预览 |
| L1 | `snip_compact()` | 消息数 >100 | 0 | 保留头 3 + 尾 97，中间替换为 `[snipped N]` 占位符。边界保护 tool_calls/tool 配对 |
| L2 | `micro_compact()` | tool 消息数 >5 | 0 | 最新 5 个完整保留，其余 >120 字符的替换为占位符 |
| L4 | `compact_history()` | 前三层后仍 >200K 字符 | 1 | 调 LLM 生成摘要，全量替换历史 + 恢复 Todo 列表。失败重试 2 次后降级 |

阈值可通过 `config.py` 中的 `CompactionConfig` 统一调整。

### 消息访问器

`agent/utils.py` 中新增 6 个归一化函数 — `get_role()`、`get_content()`、`get_tool_calls()`、`get_tool_call_id()`、`set_content()`、`to_serializable()`。兼容 `messages` 列表中的 `dict` 和 OpenAI SDK `ChatCompletionMessage` 对象，避免 `msg["role"]` 在 SDK 对象上报错。

### 权限系统

`tooling/permission/engine.py`。**实例级注入，非全局 Hook**。每个 `ToolExecutor` 构造时接收独立的 `PermissionEngine` + `Approver`，权限检查在 executor 内部完成。支持三种决策：`allow` / `deny` / `session`（本次会话记住选择）。提供公开的 grant/listener 接口（`PermissionGrant`、`set_grant_listener()`、`replace_session_rules()`）供 SessionController 持久化授权。SubAgent 与主 Agent 共享同一 executor → 共享同一 engine → 同一 session 规则。

### SubAgent

`agent/agent.py` 中的 `SubAgent(Agent)` 子类。与主 Agent 的区别：

| 属性 | 主 Agent | SubAgent |
|------|---------|----------|
| `max_steps` | 50 | 30 |
| `system_prompt` | SYSTEM_PROMPT | SUB_SYSTEM_PROMPT |
| `tool_filter` | None | `{"task", "todo_write"}` |
| `print_handler` | `default_print_handler` | `sub_print_handler` |
| `_round` 跟踪 | 无 | 有（第 30 轮注入提醒） |

创建方式：`sub = SubAgent(llm=llm, executor=executor); result = sub.run(description)`。

## 运行

```bash
# 激活环境
D:/Miniconda/envs/llm/python --version  # Python 3.12+

# 运行 Agent
cd Stage 2/project
D:/Miniconda/envs/llm/python main.py

# 运行全部测试
D:/Miniconda/envs/llm/python -m pytest tests/ -q
```

## 文档索引

| 文档 | 用途 |
|------|------|
| [.specify/memory/constitution.md](.specify/memory/constitution.md) | 9 条开发原则（最高准则） |
| [docs/architecture-philosophy.md](docs/architecture-philosophy.md) | 架构设计哲学（从 Claude Code 提炼） |
| [docs/anti-patterns.md](docs/anti-patterns.md) | 开发中踩过的坑 |
| [docs/TECH_DEBT.md](docs/TECH_DEBT.md) | 技术债追踪（触发条件 + 方案） |
| [specs/001-todo-write-tool/](specs/001-todo-write-tool/) | 迭代 1：TodoWrite |
| [specs/002-task-subagent-tool/](specs/002-task-subagent-tool/) | 迭代 2：SubAgent |
| [specs/003-context-compact/](specs/003-context-compact/) | 迭代 3：Context Compact |

## 技术栈

- **语言**: Python 3.12+
- **LLM**: DeepSeek V4 Pro（华为云 ModelArts MaaS），OpenAI 兼容 API
- **Embedding**: BGE-M3（华为云）
- **测试**: pytest
- **向量存储**: FAISS / Qdrant
- **流程**: Spec Kit（specify → clarify → plan → tasks → implement → analyze）
