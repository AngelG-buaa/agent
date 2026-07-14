# Session 持久化结构重构计划

**日期**: 2026-07-14

**目标**: 修复当前 session 初步实现中的消息持久化问题，拆分过长的
`conversation.py`，收紧模块职责，并统一 session 启动、恢复、切换和关闭流程。

本文是初步实现后的结构修正计划。与同目录 `plan.md`、`tasks.md` 中
“SessionController 与 Conversation 同文件”等旧结论冲突时，以本文为准。

## 1. 设计原则

1. 先修复消息正确性，再进行文件拆分。
2. 按职责和变化原因拆分，不按行数机械拆分。
3. `main.py` 只做参数解析和依赖装配，不实现 session 菜单或管理用例。
4. `Conversation` 只负责终端交互、命令解析和调用应用层接口。
5. `SessionController` 是 active session 生命周期和附属状态的唯一所有者。
6. `SessionManager` 是纯 SQLite Repository，不依赖 UI、Hook 或 active 状态。
7. Agent 不依赖 SessionManager，通过 scoped message sink 交付新消息。
8. 只有跨模块复用、无状态且无业务所有权的函数才进入 `utils.py`。
9. SubAgent 不接收主 SessionController 的消息出口，不持久化内部消息。
10. 新增注释和 docstring 使用中文，只解释原因和不变量。

## 2. 功能需求

### 2.1 消息持久化

- system、user、assistant tool calls、tool result 和 final assistant 全部按顺序持久化。
- 每条主会话消息只能追加一次，不允许 working context 双写。
- Repository 写入成功后才能修改内存 working context。
- 新 session 必须在创建事务中保存真实 system prompt，禁止保存空占位符。
- 恢复消息统一为 OpenAI 兼容字典。

### 2.2 Session 生命周期

- 任意时刻最多一个 active session。
- 支持新建、启动恢复、REPL 内切换、重命名、删除和退出清理。
- 切换目标完整加载成功前，当前 active 不得变化。
- 取消、重命名、拒绝删除和加载失败均不改变当前 active。
- 禁止删除 active session。
- 空 session 退出时删除数据库；非空 session 保留。

### 2.3 权限与 Todo

- 新 session 清空 session permission grants 和 Todo。
- 恢复或切换时使用目标快照完整替换权限和 Todo，空集合也必须替换。
- 用户新产生的 PermissionGrant 立即写入当前 active session。
- TodoWrite 成功后立即保存 Todo 快照；工具返回 error 时不保存。
- 新建、恢复和切换成功后重置 Todo reminder。
- Controller 关闭时注销 grant listener、Todo Hook 和 reminder Hook。

### 2.4 Transient 与 SubAgent

- `Conversation(agent)` 保留 transient 模式，不创建数据库，不支持 `/resume`。
- persistent 模式必须同时提供 SessionManager 和 PermissionEngine。
- 只提供其中一个依赖时立即报错，禁止静默退化为 transient。
- SubAgent 共享 ToolExecutor 和当前权限，但只维护自己的局部 messages。

## 3. 功能模块设计

### 3.1 `main.py`：Composition Root

保留职责：

- 解析 `--resume`。
- 创建 LLMClient、PermissionEngine、ToolExecutor、SessionManager、Agent 和 Conversation。
- 注册工具。
- 调用 `conversation.start(resume=args.resume)`。

移出 `main.py`：

- `_list_and_act()`。
- session 列表、选择、删除和重命名分支。
- `session_ui` 调用。
- 对 SessionManager 业务方法的直接调用。

### 3.2 `agent/agent.py`：Agent 核心循环

负责：

- 调用 LLM。
- 执行工具调用。
- 将所有新 assistant/tool 消息交给消息出口。

消息出口契约：

```python
emit = on_message or messages.append
emit(message)
```

`on_message` 是完整 sink，不是通知 callback。调用 sink 后 Agent 不得再次 append。

`_emit_message()` 保留在 Agent 模块，因为它表达 Agent 的消息交付语义，不是通用工具。

### 3.3 `agent/utils.py`：共享消息与输出工具

新增唯一消息归一化入口：

```python
normalize_message(message) -> dict
```

它负责把 SDK message 转为仅包含以下字段的兼容字典：

```text
role
content
tool_calls
tool_call_id
```

删除 `agent.py::_normalize_message()`，并与现有 `filter_assistant_message()` 合并，避免
同一种消息转换出现多份实现。

`_utcnow()`、`_new_message_id()`、SQL 消息序列化等 Repository 专属 helper 不进入
`utils.py`，继续留在 SessionManager 模块。

### 3.4 `agent/session_controller.py`：应用层 SessionController

新增独立模块，包含：

- `ActiveSession`。
- `ActiveSessionDeletionError`。
- `SessionController`。

Controller 负责：

- active session 生命周期。
- 主消息的“先持久化、后追加内存”出口。
- 权限 grant listener。
- Todo persistence Hook 和 disposer。
- 权限、Todo、reminder 的替换与清理。
- session 列表、重命名和删除用例。

### 3.5 `agent/conversation.py`：终端适配器

只保留：

- REPL 输入输出。
- `/exit`、`/resume` 和空输入解析。
- 启动恢复菜单及 REPL session 菜单。
- 调用 SessionController 和 Agent。
- 将已知异常转换为用户可读文本。

删除：

- SessionController 定义。
- Todo 持久化 Hook 注册。
- 测试专用 `resume_session()`。
- 对 SessionManager 的直接访问。

启动菜单和 REPL 菜单使用同一个循环式内部流程，不使用递归刷新。

### 3.6 `agent/session_manager.py`：SQLite Repository

保留：

- `SessionSummary`、`SessionSnapshot`。
- Repository 异常。
- schema、路径验证、CRUD 和事务。
- 消息、PermissionGrant、Todo 的读写。
- Repository 专属序列化 helper。

移出：

- `ActiveSession`。
- `ActiveSessionDeletionError`。

其他清理：

- schema 初始化只保留一个实现入口。
- `list_sessions()` 使用 logger，不直接打印终端文本。
- `SessionSnapshot.permissions` 明确为 `list[PermissionGrant]`。

### 3.7 `hooks.py` 与 `tools/todo_write.py`

`register_hook()` 返回幂等 disposer：

```python
register_hook(event, callback) -> Callable[[], None]
```

TodoReminderHandle 只保存 disposer，不直接读取或修改全局 `HOOKS`。

### 3.8 `agent/ui.py`

只负责：

- session 列表渲染和选择。
- 删除确认。
- 重命名输入。
- action 选择。

UI 返回数据或 action，不调用 Controller、SessionManager 或 Conversation。

## 4. 数据结构设计

### 4.1 ActiveSession

```python
@dataclass
class ActiveSession:
    id: str
    title: str
    messages: list[dict]
```

它是 Controller 的运行时状态，不属于 Repository。

### 4.2 SessionSummary

```python
@dataclass
class SessionSummary:
    id: str
    title: str
    updated_at: str
    message_count: int
```

仅用于列表展示。

### 4.3 SessionSnapshot

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

Repository 在同一连接和事务内构造完整快照。

### 4.4 PermissionGrant

```python
@dataclass(frozen=True)
class PermissionGrant:
    tool_name: str
    rule_content: str
```

Controller 和 Repository 只处理纯数据 grant，不接触 PermissionRule condition。

## 5. 模块接口设计

### 5.1 Agent

```python
Agent.run(
    messages: list[dict],
    on_message: Callable[[dict], None] | None = None,
) -> str
```

主 Agent 传入 `SessionController.append_message`；SubAgent 不传 callback。

### 5.2 SessionController

```python
start_new(system_message: dict) -> ActiveSession
resume(session_id: str) -> ActiveSession
switch(session_id: str) -> ActiveSession
append_message(message: dict) -> None
list_sessions() -> list[SessionSummary]
rename(session_id: str, title: str) -> None
delete(session_id: str) -> None
close() -> None
```

### 5.3 SessionManager

```python
create_session(system_message: dict) -> str
load_session(session_id: str) -> SessionSnapshot
append_message(session_id: str, message: dict) -> None
list_sessions() -> list[SessionSummary]
rename_session(session_id: str, title: str) -> None
delete_session(session_id: str) -> None
save_grant(session_id: str, grant: PermissionGrant) -> None
save_todos(session_id: str, todos: list[dict]) -> None
cleanup_if_empty(session_id: str) -> None
```

### 5.4 Conversation

```python
Conversation.start(resume: bool = False) -> None
```

### 5.5 Hook

```python
register_hook(event: str, callback) -> Callable[[], None]
trigger_hooks(event: str, *args) -> dict | None
```

## 6. 模块协作机制

### 6.1 主消息协作

```text
Conversation
→ Controller.append_message(user)
→ Repository.append_message(user)
→ ActiveSession.messages.append(user)
→ Agent.run(messages, on_message=Controller.append_message)
→ normalize_message(assistant/tool)
→ Controller.append_message(assistant/tool)
```

### 6.2 权限协作

```text
ToolExecutor
→ PermissionEngine.allow_for_session()
→ grant listener
→ SessionController._on_grant()
→ SessionManager.save_grant(active.id, grant)
→ PermissionEngine 安装内存规则
```

### 6.3 Todo 协作

```text
TodoWriteTool 成功
→ PostToolUse
→ SessionController persistence callback
→ snapshot_todos()
→ SessionManager.save_todos(active.id, snapshot)
```

Controller 保存 Hook disposer，关闭时统一注销。

## 7. 业务逻辑流程

### 7.1 普通启动

```text
main 解析 resume=False
→ 创建依赖
→ Conversation.start(False)
→ Controller.start_new(real system message)
→ Repository 在同一事务创建 metadata + system message
→ 清空权限和 Todo
→ 重置 reminder
→ 进入 REPL
```

### 7.2 `--resume` 启动

```text
Conversation.start(True)
→ Controller.list_sessions()
```

分支：

- 无历史：提示后创建新 session。
- 取消选择：创建新 session。
- resume：加载完整快照，成功后激活；失败则提示并创建新 session。
- rename：执行后刷新列表，继续选择。
- delete：确认后删除并刷新；拒绝删除则保持列表。

启动选择期间不存在 active session，因此不会产生幽灵数据库或错误切换。

### 7.3 普通对话轮次

```text
读取输入
→ 空输入：跳过
→ exit：退出循环
→ resume：进入切换菜单
→ 普通文本：Controller.append_message(user)
→ Agent.run(..., Controller.append_message)
→ 每条 assistant/tool 消息只经一个 sink
→ final assistant 成功持久化后打印答案
```

### 7.4 REPL 内 `/resume`

```text
保留当前 active
→ 显示列表和 action
```

分支：

- cancel：直接返回，active 不变。
- rename：更新数据库；若目标是 active，同步更新 `active.title`；刷新列表。
- delete active：拒绝并提示。
- delete 非 active：确认后删除并刷新列表。
- resume 当前 active：提示已在当前 session，不执行替换。
- resume 其他 session：先完整加载候选；成功后替换权限、Todo、reminder 和 active。
- 加载失败：显示错误，旧 active、messages、权限和 Todo 全部保持不变。

### 7.5 Controller 关闭

```text
检查 active 是否为空
→ 空 session：删除数据库
→ 非空 session：保留
→ 移除 grant listener
→ 清空 session permission rules
→ 清空 Todo
→ 注销 Todo persistence Hook
→ dispose reminder handle
→ active = None
```

使用 `try/finally` 保证某一步失败时其余资源仍释放；异常在清理完成后交给
Conversation 展示。

### 7.6 Transient 模式

```text
Conversation(agent)
→ 使用本地 messages
→ Agent.run(messages)
→ 不创建 Controller 或数据库
→ /resume 显示不支持
```

## 8. 实施顺序

1. 合并 `normalize_message()`，修复 Agent sink 双写和 final assistant 缺失。
2. 让 `register_hook()` 返回 disposer，稳定 Todo Hook 生命周期。
3. 新建 `session_controller.py`，移动 Controller、ActiveSession 和 controller 异常。
4. 修复真实 system prompt 创建、rename active、close 和 grant/Todo 持久化连接。
5. 重写 Conversation 的启动与切换菜单，删除重复和递归流程。
6. 删除 `main.py::_list_and_act()`，改为 `conversation.start(resume=args.resume)`。
7. 清理 SessionManager 的错误所有权、schema 重复和终端输出。
8. 更新少量高价值测试并运行相关回归。

## 9. 完成标准

1. `conversation.py` 不定义 SessionController，且不直接访问 SessionManager 或注册 Hook。
2. `main.py` 不包含 session 列表、删除、重命名或菜单分支。
3. `agent.py` 不再定义 `_normalize_message()`，统一使用 `agent.utils.normalize_message()`。
4. final assistant、assistant tool calls 和 tool result 都只追加一次并成功持久化。
5. 数据库中的 system prompt 与实际 Agent system prompt 一致。
6. SessionController 独立拥有 active、grant listener、Todo Hook 和 disposer。
7. 启动和 REPL 使用同一套循环式 session 菜单流程。
8. 取消和加载失败不改变旧 active 状态。
9. SubAgent 中间消息不会进入主 session。
10. `TodoReminderHandle` 不直接访问全局 `HOOKS`。
11. Repository 不打印终端文本，不包含 ActiveSession 业务状态。
12. 相关 session、conversation、permission、todo 和 Agent 回归测试通过。

## 10. 非目标

- 不改变 SQLite schema。
- 不增加工具中断恢复协议。
- 不持久化 compact working context。
- 不重构已经完成的 PermissionEngine 新架构。
- 不把 Todo 改造成并发多 session 状态容器。
- 不因文件长度继续拆分 SessionManager；只有出现第二种存储实现时再提取 Repository 接口。

