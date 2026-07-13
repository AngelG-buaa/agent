# Session Persistence Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不扩大生产文件范围的前提下，将 session 持久化重构为清晰的 Conversation、SessionController、SessionManager 三层结构。

**Architecture:** Conversation 只负责终端交互，SessionController 唯一持有 active session 并执行生命周期用例，SessionManager 只负责 SQLite。Agent 通过单次调用范围内的消息回调交付新消息，SubAgent 默认只写自己的内存上下文。

**Tech Stack:** Python 3.12+、标准库 `sqlite3`、`dataclasses`、`contextlib.closing`、现有 Agent/Tool/Permission/Hook 基础设施。

**设计依据:** `specs/005-session-persistence/architecture-design.md`

---

## 实施原则

1. 优先保证职责边界和状态不变量，不为测试方便污染生产接口。
2. 不新增顶层目录，不重组 RAG、LLM、compact、ToolRegistry 或配置模块。
3. `main.py` 只装配依赖，不出现 session 状态和业务分支。
4. 不使用 session 生命周期全局 Hook，不访问其他对象的私有字段。
5. 只保留少量高价值回归测试，其余通过代码审查和人工场景验证。
6. 新增注释和 docstring 使用中文。
7. 注释只解释“为什么”和关键不变量，不写逐行翻译式注释。
8. 每完成一个任务先阅读 diff，确认没有把职责推到错误模块，再进入下一任务。

## 文件范围

允许修改的生产文件：

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
```

允许修改的 feature 文件：

```text
README.md
specs/005-session-persistence/
tests/test_session_manager.py
tests/test_session_persistence.py
```

`tests/test_conversation.py` 仅允许在 FakeAgent 签名无法适配新的 `Agent.run(..., on_message=...)` 契约时做最小修改，不改变测试意图。

---

### Task 1: 建立公开的权限与执行器契约

**Files:**
- Modify: `tooling/permission/engine.py`
- Modify: `tooling/executor.py`

- [ ] **Step 1: 在 PermissionEngine 中定义精确授权值对象**

```python
@dataclass(frozen=True)
class PermissionGrant:
    tool_name: str
    rule_content: str
```

授权持久化必须保存 `tool_name + rule_content`，不能只保存工具名，恢复时不能扩大成 `"*"`。

- [ ] **Step 2: 用公开 listener 替代 `_save_callback`**

PermissionEngine 最终公开接口：

```python
set_grant_listener(listener: Callable[[PermissionGrant], None] | None) -> None
replace_session_rules(grants: list[PermissionGrant], *, notify: bool = False) -> None
clear_session_rules() -> None
```

`allow_for_session()` 新增 `notify: bool = True`。用户产生授权时通知 listener；恢复授权时 `notify=False`，避免把加载行为重新写库。

- [ ] **Step 3: ToolExecutor 改为构造注入 PermissionEngine**

`build_tool_executor()` 新增参数：

```python
permission_engine: PermissionEngine | None = None
```

传入时使用该实例，未传时保持原来的自动创建行为。删除 `executor._permission_engine` 和 `engine._save_callback`。

- [ ] **Step 4: 审查本任务 diff**

确认 PermissionEngine 不 import SessionManager，ToolExecutor 不暴露新的可变权限状态，原有 deny > allow > ask 顺序未改变，新增注释均为中文。

建议检查点提交：

```powershell
git add tooling/permission/engine.py tooling/executor.py
git commit -m "refactor: expose session permission grants"
```

---

### Task 2: 将 SessionManager 重写为纯 SQLite Repository

**Files:**
- Rewrite: `agent/session_manager.py`
- Simplify: `tests/test_session_manager.py`

- [ ] **Step 1: 定义最小数据模型和错误类型**

```text
SessionSummary
SessionSnapshot
SessionNotFound
SessionCorrupted
SessionPersistenceError
```

`SessionSnapshot` 一次返回 metadata、messages、permissions、todos，Controller 不得调用四个 load 方法在外部拼装半份快照。

- [ ] **Step 2: 删除长连接缓存和伪 context manager**

删除 `_connections`、`get_connection()`、`close()`。每次数据库操作使用：

```python
with closing(sqlite3.connect(path)) as conn:
    conn.execute("PRAGMA foreign_keys = ON")
```

事务在连接关闭前显式 commit/rollback。添加中文注释说明：`sqlite3.Connection` 自身的上下文协议只管理事务，不负责关闭文件句柄。

- [ ] **Step 3: 建立 schema 与数据库不变量**

```text
PRAGMA user_version = 1
UNIQUE(messages.session_id, messages.seq)
permissions 主键包含 rule_content
todos 使用 position 保留顺序
所有子表外键指向 sessions(id)
```

当前功能尚未提交，不实现旧草稿数据库迁移；版本不匹配抛出 `SessionCorrupted`。

- [ ] **Step 4: 实现原子创建和消息追加**

```python
create_session(system_message) -> SessionSnapshot
append_message(session_id, message) -> dict
load_session(session_id) -> SessionSnapshot
```

`create_session()` 在同一事务中创建 metadata 和 system message，失败时删除半成品数据库。

`append_message()` 顺序固定：

```text
BEGIN IMMEDIATE
→ 计算 seq
→ INSERT message
→ 更新 title、updated_at、message_count
→ COMMIT
```

添加中文注释解释“先持久化，后更新内存”的原因。

- [ ] **Step 5: 实现 session 管理与附属状态**

```python
list_sessions() -> list[SessionSummary]
rename_session(session_id, title) -> None
delete_session(session_id) -> None
cleanup_if_empty(session_id) -> None
add_permission(session_id, grant) -> None
replace_todos(session_id, todos) -> None
```

要求：session id 先经 `uuid.UUID()` 校验；列表按 `updated_at` 排序；损坏数据库在连接关闭后跳过；空 Todo 也执行 DELETE；空 session 离开连接作用域后再删除文件。

- [ ] **Step 6: 将测试收缩为三个核心案例**

1. create + append + load 往返，角色顺序为 `system/user/assistant`。
2. permission 的 `rule_content` 和空 Todo 可以往返。
3. 损坏数据库被跳过后文件可以删除，证明没有连接泄漏。

不测试私有方法、SQLite 内部索引名称或每个 getter。

```powershell
D:/Miniconda/envs/llm/python.exe -m pytest tests/test_session_manager.py -q -p no:cacheprovider
```

- [ ] **Step 7: 审查职责边界**

确认 `agent/session_manager.py` 只包含持久化职责，没有 UI、Agent、Hook 或 PermissionEngine 行为编排。

建议检查点提交：

```powershell
git add agent/session_manager.py tests/test_session_manager.py
git commit -m "refactor: make session storage transactional"
```

---

### Task 3: 为 Agent 建立单次调用范围内的消息出口

**Files:**
- Modify: `agent/agent.py`
- Optional minimal update: `tests/test_conversation.py`

- [ ] **Step 1: 调整 Agent.run 契约**

```python
run(
    messages: list,
    on_message: Callable[[dict | object], None] | None = None,
) -> str
```

方法开始处只定义一次 `emit = on_message or messages.append`。

- [ ] **Step 2: 所有 Agent 生成消息统一经过 emit**

`assistant tool_calls`、每条 `tool result`、`final assistant` 都必须调用 `emit()`。删除 `MessageAppended` 全局 Hook。PreLLMCall 注入的 reminder 仍只属于 working context，不进入原始 transcript。

- [ ] **Step 3: 保持 SubAgent 隔离**

SubAgent 继续调用 `run(messages)`，不接收主 SessionController callback。其 `_execute_tool_calls()` 只把父类传入的 `emit` 原样转交。

添加中文注释说明：默认 sink 保证 SubAgent 中间消息只进入子上下文。

- [ ] **Step 4: 必要时同步 FakeAgent**

如果 `tests/test_conversation.py` 的 FakeAgent 因签名不匹配失败，只增加可选 `on_message` 参数并使用它，不改变测试内容。

- [ ] **Step 5: 小范围验证**

```powershell
D:/Miniconda/envs/llm/python.exe -m pytest tests/test_conversation.py tests/test_task.py -q -p no:cacheprovider
```

代码阅读必须确认 final assistant 现在确实进入 messages，不再依赖 FakeAgent 伪造该行为。

建议检查点提交：

```powershell
git add agent/agent.py tests/test_conversation.py
git commit -m "refactor: scope agent message emission"
```

---

### Task 4: 稳定 Todo 与 Hook 生命周期

**Files:**
- Modify: `tools/todo_write.py`
- Modify: `hooks.py`

- [ ] **Step 1: 为 Todo 提供稳定公开接口**

```python
snapshot_todos() -> list[dict]
replace_todos(todos: list[dict]) -> None
```

`snapshot_todos()` 返回防御性浅拷贝。`replace_todos()` 必须 `clear() + extend()`，禁止重新绑定 `CURRENT_TODOS`。保留 `restore_todos()` 时只委托 `replace_todos()`。

- [ ] **Step 2: Hook 注册返回 disposer**

`register_hook()` 返回幂等注销函数。现有调用方可忽略返回值，SessionController 保存 Todo hook 的 disposer 并在 close 时调用。

- [ ] **Step 3: 删除 session 状态事件**

```text
SessionStart
SessionEnd
MessageAppended
ResumeRequested
```

保留工具与 Agent 扩展事件。Hook 不再承担 session 创建、切换或清理。

- [ ] **Step 4: 审查全局状态边界**

只允许 `CURRENT_TODOS` 作为当前单 active CLI 的明确妥协。新增中文注释说明该边界，不预留并发 session 抽象。

建议检查点提交：

```powershell
git add tools/todo_write.py hooks.py
git commit -m "refactor: stabilize todo and hook state"
```

---

### Task 5: 在 Conversation 模块内实现 SessionController

**Files:**
- Modify: `agent/conversation.py`
- Simplify: `tests/test_session_persistence.py`

- [ ] **Step 1: 定义轻量 ActiveSession**

```python
@dataclass
class ActiveSession:
    id: str
    title: str
    messages: list
```

它只保存运行态，不执行 SQL、不处理终端输入。

- [ ] **Step 2: 建立 SessionController 的唯一入口**

```python
start_new()
resume(session_id)
switch(session_id)
send(user_input)
append_message(message)
list_sessions()
rename(session_id, title)
delete(session_id)
close()
```

Controller 只持有 agent、session_manager、permission_engine、active 和 Todo hook disposer。

- [ ] **Step 3: 实现先准备、后激活的恢复协议**

`resume()` 和 `switch()` 先加载完整候选快照。成功后才依次替换 permission rules、Todo（包括空列表）、ActiveSession。

添加中文注释说明：候选快照先完整加载，才能保证失败时当前 session 完全不变。

- [ ] **Step 4: 实现持久化优先的 append_message**

```text
SessionManager.append_message()
→ active.messages.append()
```

持久化失败时不修改 working context。该方法作为主 Agent 的 `on_message` callback。

- [ ] **Step 5: 连接权限和 Todo**

- PermissionEngine listener 指向当前 Controller 的精确授权持久化方法。
- PostToolUse 仅在 `todo_write` 成功时保存 `snapshot_todos()`。
- 空 Todo 也写库。
- close 时清除 listener、权限规则、Todo，并注销 Hook。

- [ ] **Step 6: 实现安全生命周期**

- `start_new()` 只创建一次 session，并立即带有 system message。
- `resume()` 不创建数据库。
- `delete()` 拒绝删除当前 active session。
- `close()` 清理空 session，且可重复调用。
- transient 模式仍走同一个 Controller，只是不提供持久化管理操作。

- [ ] **Step 7: 仅保留两个 Controller 级回归测试**

1. resume 后继续对话，数据库文件数量不增加，消息继续写原 id。
2. A/B session 切换后权限和空 Todo 不串线，SubAgent 消息不进入主 session。

测试应经过 Controller 和真实 Agent callback，不允许手工逐条调用 `save_message()` 冒充端到端。

```powershell
D:/Miniconda/envs/llm/python.exe -m pytest tests/test_session_persistence.py -q -p no:cacheprovider
```

- [ ] **Step 8: 审查类的体积和命名**

如果 SessionController 出现 UI 分支或 SQL，立即移回 Conversation 或 SessionManager。每个方法保持浅层控制流，不为减少行数写晦涩闭包。

建议检查点提交：

```powershell
git add agent/conversation.py tests/test_session_persistence.py
git commit -m "feat: add explicit session controller"
```

---

### Task 6: 收口 Conversation、UI 与 main.py

**Files:**
- Modify: `agent/conversation.py`
- Modify: `agent/ui.py`
- Rewrite session portion: `main.py`

- [ ] **Step 1: Conversation 只保留终端行为**

`Conversation.start(resume=False)` 负责启动选择、读取输入、过滤空输入、解析 `/exit` 和 `/resume`、调用 Controller、显示结果，并在 `finally` 中 close。

Conversation 不直接访问 SQLite、权限规则容器或 Todo 列表。

- [ ] **Step 2: 实现两阶段 session 菜单**

REPL 内 `/resume` 时，当前 active session 在用户完成选择前保持不变：

```text
取消 -> 不变
重命名 -> 不变并刷新列表
拒绝删除 -> 不变
加载失败 -> 不变
打开成功 -> 一次切换
```

启动 `--resume` 没有历史或用户取消时才创建新 session。

- [ ] **Step 3: 修复 UI 快捷键**

```text
[O]pen  [D]elete  [R]ename  [C]ancel
```

UI 函数只返回 action 或用户输入，不调用 SessionManager。

- [ ] **Step 4: 将 main.py 压缩为组装点**

```text
解析 --resume
创建 LLM
创建 PermissionEngine
将 engine 注入 ToolExecutor
注册 tools 和原有 todo reminder
创建 SessionManager
创建 Agent
Conversation(agent, manager, engine).start(resume=args.resume)
```

删除所有 handler factory、`sid_ref`、`trigger_hooks`、`sys.exit` 和私有字段访问。

- [ ] **Step 5: 审查可读性**

- `main.py` 主流程一屏可读。
- Conversation 不含持久化细节。
- UI 文案和 action 名称一致。
- 退出路径全部经过同一个 `finally`。
- Ctrl+C 的第二次中断不会绕过 session 清理。

建议检查点提交：

```powershell
git add agent/conversation.py agent/ui.py main.py
git commit -m "refactor: move session lifecycle out of main"
```

---

### Task 7: 文档、静态检查与人工验收

**Files:**
- Modify: `README.md`
- Modify: `specs/005-session-persistence/plan.md`
- Modify: `specs/005-session-persistence/research.md`
- Modify: `specs/005-session-persistence/tasks.md`
- Verify: all scoped files

- [ ] **Step 1: 标记旧 Hook 架构已废弃**

在旧文档顶部链接 `architecture-design.md` 和 `implementation-plan.md`。不重写历史内容，只防止后续实现者继续执行被否决方案。

- [ ] **Step 2: 更新 README**

说明 `python main.py`、`python main.py --resume`、`/resume`，以及权限/Todo 按 session 隔离、空 session 退出清理、active session 运行时不可删除。

- [ ] **Step 3: 运行最小自动化验证**

```powershell
D:/Miniconda/envs/llm/python.exe -m pytest tests/test_session_manager.py tests/test_session_persistence.py -q -p no:cacheprovider
```

再运行现有关键回归：

```powershell
D:/Miniconda/envs/llm/python.exe -m pytest tests/test_conversation.py tests/test_task.py tests/test_todo_write.py -q -p no:cacheprovider
```

不为追求覆盖率新增更多测试。

- [ ] **Step 4: 运行静态架构检查**

```powershell
Get-ChildItem main.py,agent,tooling,tools -Recurse -File -Filter *.py | Select-String -Pattern 'sid_ref|_permission_engine|_save_callback|SessionStart|SessionEnd|MessageAppended|ResumeRequested'
```

期望生产代码无匹配。

```powershell
git diff --check
```

- [ ] **Step 5: 完成人工场景验收**

1. 新会话问答后退出，数据库包含 system/user/final assistant。
2. `--resume` 后追问，仍写入原数据库，不新增幽灵 session。
3. `/resume` 取消后继续对话，当前 session 未变化。
4. A session 的权限和 Todo 切换到 B 后不生效，切回 A 后恢复。
5. SubAgent 执行工具后，主 session 没有子 Agent 中间消息。
6. 空 session 立即退出后数据库被删除且 Windows 无文件锁。

- [ ] **Step 6: 进行最终代码阅读**

逐个回答：

- 谁拥有 active session？只能是 SessionController。
- 谁执行 SQL？只能是 SessionManager。
- 谁处理终端输入？只能是 Conversation/UI。
- 谁决定消息写到哪个 session？只能是当前 Controller callback。
- 谁恢复权限和 Todo？只能是 Controller 激活流程。

任何答案出现两个模块，都先修正职责边界再结束。

建议最终提交：

```powershell
git add README.md specs/005-session-persistence main.py hooks.py agent tooling tools tests/test_session_manager.py tests/test_session_persistence.py
git commit -m "feat: add isolated persistent agent sessions"
```

不要使用 `git add .`，避免暂存工作区中的无关变化。

---

## 完成标准

- [ ] `main.py` 没有 session 状态或业务分支。
- [ ] 恢复不会创建新数据库。
- [ ] system、user、tool-call assistant、tool result、final assistant 均从唯一入口持久化。
- [ ] SubAgent 中间消息不进入主 session。
- [ ] 权限恢复保留精确 `rule_content`。
- [ ] 空权限和空 Todo 能覆盖旧状态。
- [ ] 取消、重命名、拒绝删除和加载失败不改变 active session。
- [ ] SQLite 无长连接缓存和 Windows 文件锁泄漏。
- [ ] 新增注释和 docstring 使用中文，且只解释原因和不变量。
- [ ] 修改范围没有扩展到未批准的生产文件。
