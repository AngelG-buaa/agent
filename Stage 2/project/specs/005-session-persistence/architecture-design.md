# Session 持久化架构设计

**Feature**: `005-session-persistence`
**Date**: 2026-07-13
**Status**: Approved, revised after architecture review
**Scope**: 仅重构当前 session 功能已经修改或新增的文件

## 1. 文档地位

本文档替代本 feature 现有 `plan.md`、`research.md`、`tasks.md` 和
`implementation-plan.md` 中与本文冲突的设计结论：

- 通过全局 Hook 编排 Session 生命周期
- 在 `main.py` 中保存当前 session id 并桥接各组件
- 通过 `executor._permission_engine`、`engine._save_callback` 访问私有状态
- 使用 `SessionStart`、`SessionEnd`、`MessageAppended`、`ResumeRequested` 承担状态迁移

功能规格仍以 `spec.md` 为准；若其架构描述与本文冲突，以本文为准。
实例级权限门禁的完整设计见
[`docs/permission-executor-architecture.md`](../../docs/permission-executor-architecture.md)，它是
SessionController 实施的前置条件。

## 2. 设计目标

1. 新建、恢复、切换和退出具有唯一、可验证的生命周期。
2. `system`、`user`、所有 `assistant` 和 `tool` 消息均按原始顺序即时持久化。
3. 主 Agent、SubAgent、不同 session 的消息、权限和 Todo 严格隔离。
4. `main.py` 只做参数解析和对象装配，不实现 session 用例。
5. SQLite 写入与内存状态保持一致；持久化失败时内存不得先行变化。
6. 不修改当前未提交文件集合之外的生产模块，不进行全项目目录重组。
7. Agent 的 Think-Act-Observe 循环保持无 session、SQLite 和 CLI 依赖。
8. 恢复后的消息统一满足当前 OpenAI 兼容接口的消息字典契约。

## 3. 非目标

- 不支持同时运行多个 active session。
- 不支持多个进程同时写同一个 session。
- 不引入事件溯源、消息总线、ORM 或新的第三方依赖。
- 不把模块级 Todo 状态扩展为并发安全的多 session 容器。
- 不处理工具已经产生外部副作用、但 tool result 尚未落库时的恢复语义。
- 不引入通用 RuntimeState 或持久化 Todo reminder 计数等临时运行态。
- 不保存 compact 后的 working context；恢复时以原始 transcript 重新构建上下文。
- 不重构 RAG、LLMClient、compact、ToolRegistry 或配置系统。
- 不为未提交草稿阶段生成的旧数据库编写迁移器。

## 4. 根因与设计决策

### 4.1 根因

Session 持久化被错误归类为纯横切关注点。日志和遥测可以通过通知型 Hook 扩展；但 session 创建、恢复、切换、权限隔离和 Todo 替换是带有强不变量的核心状态迁移。

原方案只消除了模块间的直接 import，却把真实耦合转移成：

```text
sid_ref 闭包
+ 全局 HOOKS
+ 私有字段访问
+ 模块级 Todo
+ main.py 中的状态分支
```

这属于语法解耦、语义耦合。正确方向是建立一个显式的 session 应用边界。

### 4.2 方案选择

采用局部三层结构：

```text
Conversation        REPL 与终端交互
      ↓
SessionController   active session 与应用用例
      ↓
SessionManager      纯 SQLite Repository
```

不采用以下方案：

- 继续堆叠 Hook：缺少 session 作用域，无法表达原子切换。
- 大型贫血 Service：容易把 `main.py` 的职责迁移成另一个 God Object。
- 事件溯源：对当前单用户 CLI 过度设计。

## 5. 文件范围

本设计允许修改以下当前已变更或新增的文件：

```text
main.py
hooks.py
agent/agent.py
agent/conversation.py
agent/session_manager.py
agent/ui.py
tooling/executor.py
tooling/permission/engine.py
tools/todo_write.py
tests/test_session_manager.py
tests/test_session_persistence.py
```

`README.md` 和本 feature 的规格文档可按最终行为更新。其他生产代码原则上不修改。

为避免无必要地修改现有干净测试，Conversation 使用以下唯一构造入口：

```python
Conversation(
    agent,
    session_manager: SessionManager | None = None,
    permission_engine: PermissionEngine | None = None,
)
```

Conversation 始终创建同一个 SessionController。`session_manager is None` 表示 transient 模式：Controller 仍执行相同的消息和生命周期状态机，但不提供列表、恢复、重命名和删除能力，消息只写 working context。生产入口必须同时传入 SessionManager 和 PermissionEngine。该模式只用于现有无持久化调用和单元测试，不存在第二套状态机。

## 6. 组件职责

### 6.1 `main.py`

职责仅限：

1. 解析 `--resume`。
2. 创建 LLM、PermissionEngine、ToolExecutor、Agent 和 SessionManager。
3. 通过 Conversation 构造参数传入公开的 session 依赖。
4. 启动 REPL。

禁止：

- 保存当前 session id。
- 实现恢复、切换、重命名或删除分支。
- 注册 session 生命周期 Hook。
- 访问任何 `_private_field`。

### 6.2 `Conversation`

`Conversation` 是终端适配器，负责：

- `input()` 与 `print()`。
- `/exit`、`/resume` 和空输入解析。
- 调用 SessionController。
- 在 CLI 边界把已知异常转为用户可读消息。

它不直接执行 SQL，不直接修改权限规则或 Todo 全局状态。

Conversation 构造时创建唯一的 SessionController；Controller 在初始化时通过 PermissionEngine 的公开 listener 接口连接权限持久化。main.py 不需要获得 Controller 引用。

### 6.3 `SessionController`

`SessionController` 位于 `agent/conversation.py`，是唯一的 session 应用入口：

```python
start_new() -> ActiveSession
resume(session_id: str) -> ActiveSession
switch(session_id: str) -> ActiveSession
send(user_input: str) -> str
list_sessions() -> list[SessionSummary]
rename(session_id: str, title: str) -> None
delete(session_id: str) -> None
close() -> None
```

它拥有唯一的 `active: ActiveSession | None`，并维护以下不变量：

- 任意时刻最多一个 active session。
- 新建和恢复是两个不同操作。
- 所有主会话原始消息只经过 `append_message()`。
- 切换权限前清空旧 session 规则。
- 切换 Todo 时空列表也必须覆盖旧状态。
- 新建、恢复和切换成功后重置 Todo reminder 计数。
- 取消或加载失败不改变 active session。
- active session 在运行期间不可删除。
- 切换到当前 active session 是无副作用操作。

### 6.4 `ActiveSession`

`ActiveSession` 是轻量运行态数据：

```python
@dataclass
class ActiveSession:
    id: str
    title: str
    messages: list[dict]
```

`messages` 是允许 compact 原地修改的 working context。原始 transcript 已即时写入 SQLite，因此 working context 和 durable transcript 是有意分离的两个视图。

恢复语义是“原始 transcript 重放”，不是 working context 的逐字节 checkpoint。恢复时加载
完整原始消息；下一次 LLM 调用前仍由现有 compact 管线按需压缩，因此不保证摘要文本与退出前
完全一致。

### 6.5 `SessionManager`

保留类名以缩小改动，但语义严格收窄为 SQLite Repository。它只负责：

- schema 初始化与版本检查。
- session CRUD。
- 消息、权限和 Todo 的事务读写。
- 列表查询和空 session 清理。

它不知道 Conversation、Agent、Hook、PermissionEngine 或 TodoWriteTool 的存在。

完整恢复使用一个明确快照，而不是多次独立 load 后在外部拼装：

```python
@dataclass
class SessionSnapshot:
    id: str
    title: str
    updated_at: str
    message_count: int
    messages: list[dict]
    permissions: list[PermissionGrant]
    todos: list[dict]
```

`load_session(session_id) -> SessionSnapshot` 在同一个只读连接和事务中读取全部状态，确保 Controller 不会拿到来自不同时点的半份快照。

### 6.6 `Agent`

Agent 保持无状态。接口调整为：

```python
run(
    messages: list[dict],
    on_message: Callable[[dict], None] | None = None,
) -> str
```

语义：

- `on_message is None` 时使用 `messages.append`，适用于 SubAgent 和无持久化测试。
- 主 Agent 由 SessionController 传入 session 级回调。
- LLM 返回的 SDK message 在 Agent 内只归一化一次，再以兼容 dict 交给消息出口。
- Agent 产生的 tool-call assistant、tool result 和 final assistant 全部调用该回调。
- Agent 不触发 `MessageAppended` 全局 Hook。
- SubAgent 永远不接收主 SessionController 的回调，其 system、user、assistant 和 tool 消息
  全部只存在于本次子任务的局部列表。

Agent 与 Repository 之间统一使用 OpenAI 兼容消息字典。允许的字段只有：

```text
role
content
tool_calls
tool_call_id
```

不存在 `tool_name` 恢复字段，也不在持久化链中混用 SDK message object。工具名如需展示，
只能从前置 assistant message 的 `tool_calls` 临时推导，不得写回发送给 LLM 的消息。

## 7. 消息提交协议

主 session 的所有消息使用同一个入口：

```python
SessionController.append_message(message)
```

提交顺序不可交换：

```text
1. Repository 开启事务
2. 计算并写入 seq
3. 更新 updated_at、message_count 和必要的 title
4. COMMIT
5. 将消息追加到 working context
```

如果 1 至 4 任一步失败，内存 messages 不得增加该消息。

### 7.1 新 session

`create_session(system_message)` 在一个事务内完成：

- 创建 schema。
- 插入 sessions 行。
- 插入 seq=0 的 system message。
- 设置 `message_count=1`。

数据库不能出现 schema 存在但 session metadata 或 system message 缺失的半成品。

### 7.2 普通对话轮次

```text
过滤空输入
→ append user
→ Agent.run(messages, on_message=append_message)
   → append assistant tool_calls
   → append each tool result
   → append final assistant
→ 输出 final assistant
```

最终 assistant 必须先提交成功，再向终端宣告该轮完成。

### 7.3 SubAgent

SubAgent 不接收主 SessionController 的 `on_message`，使用自己的 `messages.append`。它的 system、user、assistant 和 tool 消息全部只存在于子上下文；主 session 只保存 task 工具本身的调用和最终工具结果。

## 8. 生命周期状态机

稳定状态只有：

```text
NoActive
Active(session_id)
```

### 8.1 启动新会话

```text
NoActive
→ repository.create_session(system_message)
→ replace permission rules with empty set
→ replace todos with empty list
→ Active(new_id)
```

### 8.2 `--resume`

```text
list sessions
→ 无历史：提示后 start_new
→ open：完整 load 后激活，不预先创建 session
→ rename/delete：执行后刷新列表
→ cancel：start_new
```

启动恢复阶段尚无 active session。只有用户明确打开历史 session 时才调用 `resume()`；该路径
绝不调用 `start_new()`，也不触发 SessionStart。重命名或删除后继续显示列表，不创建临时
session。没有历史或用户取消恢复时才创建新 session。

### 8.3 REPL 内 `/resume`

当前 session 保持 Active，直到目标候选完整加载成功：

```text
显示列表
→ 用户取消：active 不变
→ rename / 拒绝删除：active 不变并刷新列表
→ 删除其他历史 session：active 不变并刷新列表
→ load target 失败：active 不变
→ target 是当前 active：不执行切换
→ load target 成功：重置 reminder，并一次性替换 permissions、todos、messages、id
```

当前消息已逐条提交，因此显示菜单和切换前都不调用 SessionEnd，也不需要全量保存。
旧 active 只有在目标快照完整加载并校验成功后才被替换；任何菜单操作或异常都不能让
Controller 进入“messages 仍在、但 active id 已清空”的半激活状态。

运行期间禁止删除 active session。可以删除其他历史 session。启动阶段还没有 active session，因此可删除任意历史 session。

### 8.4 退出

```text
controller.close()
→ active 无 user message：删除数据库
→ active 有 user message：保留数据库
→ active = None
```

正常 `/exit`、输入阶段 Ctrl+C/EOF、连续中断退出均必须经过同一个 `finally: controller.close()` 路径。

## 9. SQLite 设计

### 9.1 Schema

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL,
    message_count INTEGER NOT NULL
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    UNIQUE(session_id, seq),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE permissions (
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    rule_content TEXT NOT NULL,
    PRIMARY KEY(session_id, tool_name, rule_content),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE todos (
    session_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    active_form TEXT NOT NULL,
    PRIMARY KEY(session_id, position),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
```

Todo 使用 `position` 保留顺序并允许相同 content。权限保存精确 `rule_content`，恢复时禁止扩大为 `*`。

### 9.2 事务与连接

- 每个公开 Repository 方法使用 `contextlib.closing(sqlite3.connect(...))` 保证关闭连接。
- 写事务在 closing 内使用 `with conn:` 统一 commit/rollback；需要分配 seq 的事务在该块开头执行 `BEGIN IMMEDIATE`。
- 不维护 `_connections` 缓存。
- 写入 seq 使用 `BEGIN IMMEDIATE`。
- `messages` 的 `(session_id, seq)` 唯一约束是最后防线。
- 每次连接启用 `PRAGMA foreign_keys = ON`。
- schema 初始化设置 `PRAGMA user_version = 1`。
- `updated_at` 由 Python 生成带时区的 UTC ISO-8601。
- `list_sessions()` 读取 metadata 后按 `updated_at` 降序排序，不使用 mtime。
- 所有 SQL 使用参数化查询。

### 9.3 路径安全

所有外部 session id 先通过 `uuid.UUID()` 校验。构造数据库路径后必须确认 resolved path 位于 `sessions_dir` 下，避免通过外部修改数据库 metadata 造成路径越界。

### 9.4 草稿数据库兼容

该 feature 尚未提交，不实现旧草稿 schema 的自动迁移。发现 `user_version` 或 schema 不兼容时抛出 `SessionCorrupted`，由用户删除旧测试数据库后重新创建。禁止静默修补或猜测字段。

## 10. 权限隔离

本节依赖 `docs/permission-executor-architecture.md` 已完成：权限门禁属于 ToolExecutor
实例，不再注册为进程级 `PreToolUse` Hook。SubAgent 与主 Agent 共享同一个 executor，
因此共享当前 active session 的权限；这不意味着共享消息持久化出口。

新增值对象：

```python
@dataclass(frozen=True)
class PermissionGrant:
    tool_name: str
    rule_content: str
```

PermissionEngine 提供公开接口：

```python
set_grant_listener(callback) -> None
replace_session_rules(grants, notify=False) -> None
clear_session_rules() -> None
```

规则：

- 用户产生新 session allow 时，listener 通知 Controller 持久化精确 grant。
- 恢复时使用 `notify=False`，不得把加载行为当成新授权。
- 切换先替换为目标 session 的完整 grants；目标为空即清空。
- 禁止 `executor._permission_engine` 和 `engine._save_callback`。
- ToolExecutor 通过构造参数接收 engine；组装层已经持有该 engine，不从 executor 反向读取。
- 实例级权限重构未完成前，不得开始实现 SessionController。

## 11. Todo 隔离

`tools/todo_write.py` 增加：

```python
snapshot_todos() -> list[dict]
replace_todos(todos: list[dict]) -> None
register_todo_hooks() -> TodoReminderHandle
```

`replace_todos()` 使用 `CURRENT_TODOS.clear()` 和 `extend()`，不得重新绑定列表。

Todo 持久化可以继续使用已有 `PostToolUse`：

- 仅在 `todo_write` 成功后执行。
- 保存 `snapshot_todos()`，不盲信输入 params。
- 空列表也必须调用 `replace_todos(session_id, [])`。

`TodoReminderHandle` 只暴露幂等的 `reset()` 和 `dispose()`：

- 新建 session 后调用 `reset()`。
- 候选 session 激活成功时调用 `reset()`，加载失败或取消时不调用。
- Controller 关闭时调用 `dispose()`，避免重复注册 Hook。
- reminder 计数不持久化；恢复后从零开始。

模块级 Todo 是本次范围内的明确妥协：CLI 同时只有一个 active session，且 SubAgent 禁止 TodoWrite，因此可以正确隔离。未来支持并发 session 时，再单独引入注入式 TodoState。

## 12. Hook 边界

删除本 feature 新增的 session 状态事件：

```text
SessionStart
SessionEnd
MessageAppended
ResumeRequested
```

已有工具管线 Hook 可以保留，但权限门禁除外：

```text
PreToolUse
PostToolUse
PreLLMCall
PostRound
PreAgentStop
```

判定规则：

- 通知、拦截和工具扩展可用 Hook。
- `PreToolUse` 不得承载 PermissionEngine；权限检查是 ToolExecutor 的实例级固定门禁。
- 创建、恢复、切换、权限替换等状态迁移必须使用显式方法调用。
- Hook payload 如果没有 session 作用域，不得写 session 数据，TodoWrite 的单 active CLI 例外已在本设计中显式记录。

## 13. 错误模型

内部定义少量明确异常：

```text
SessionNotFound
SessionCorrupted
SessionPersistenceError
ActiveSessionDeletionError
```

规则：

- Repository 捕获 `sqlite3.Error`，转换为上述异常并使用异常链保留根因。
- Repository 不打印交互文本。
- Controller 不吞持久化异常，不提交半完成的 active 状态。
- Conversation 在 CLI 边界显示用户可读错误。
- resume/switch 失败时保留原 active session。
- 损坏数据库列表项使用 `contextlib.closing` 关闭连接后跳过并警告，Windows 下不得残留文件锁。

## 14. 测试设计

本次不追求覆盖率，只保护最容易再次破坏的架构不变量。测试不得迫使生产代码暴露私有方法或增加无业务价值的抽象。

### 14.1 Repository 核心测试

只保留三个案例：

1. create + append + load 往返，角色顺序为 `system/user/assistant`。
2. PermissionGrant 的 `rule_content` 与空 Todo 正确往返。
3. 损坏数据库被跳过后文件可删除，证明 Windows 下没有连接泄漏。

### 14.2 Controller 核心测试

只保留三个案例：

1. resume 后继续对话，不新增数据库，消息写入原 session id。
2. `/resume` 取消、重命名和目标加载失败均不改变当前 active session。
3. A/B session 权限、空 Todo 和 reminder 计数不串线，SubAgent 中间消息不进入主 session。

Controller 测试必须经过真实消息链：

```text
Conversation
→ SessionController
→ Agent
→ on_message
→ SessionManager
```

禁止通过手工调用多次 `save_message()` 冒充端到端测试。

### 14.3 人工验收

通过新建、`--resume`、`/resume` 取消、A/B 切换、SubAgent 工具调用和空 session 退出六个场景验证终端行为。与本 feature 无关的既有失败单独记录，不借机修改干净模块。

## 15. 验收条件

1. `main.py` 不再包含 session handler 闭包、`sid_ref` 或恢复分支。
2. grep 不再发现 session 代码访问 `_permission_engine` 或 `_save_callback`。
3. 恢复 session 后数据库文件数量不增加。
4. 简单无工具轮的持久化角色顺序为 `system, user, assistant`。
5. 工具轮包含 tool-call assistant、全部 tool result 和 final assistant。
6. SubAgent 中间消息不会出现在主 session。
7. 恢复消息只包含兼容字段，不存在 `tool_name` 或 SDK object。
8. A session 的权限、Todo 和 reminder 计数不会在 B session 生效。
9. 取消、重命名、拒绝删除和恢复失败均不改变当前 active session。
10. 损坏数据库不会在 Windows 留下文件锁。
11. Session 相关新增和回归测试全部通过。

## 16. 延后事项

以下事项只有出现真实需求时才进入独立 feature：

- 并发 active sessions。
- 注入式 TodoState。
- 通用 RuntimeState 和临时运行态持久化。
- 工具副作用已发生但 tool result 未落库时的恢复协议。
- compact working context checkpoint；当前只恢复原始 transcript。
- 单 SQLite catalog 优化大量 session 列表。
- 数据库 schema 自动迁移。
- 跨进程同 session 写入协调。
- 将 Conversation、SessionController、Repository 移入新的分层目录。

这些延后事项不得在本次实现中预留抽象或加入未使用配置。
