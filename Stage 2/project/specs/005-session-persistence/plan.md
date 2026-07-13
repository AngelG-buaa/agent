# Implementation Plan: Session 持久化

**Branch**: `005-session-persistence` | **Date**: 2026-07-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-session-persistence/spec.md`

## Summary

为 myAgent 添加 Session 持久化功能。核心设计：新增 `SessionManager` 类封装 SQLite 存储（每 session 一个 `.db` 文件），通过 **Hook 系统**与 `Conversation` 连接——两者彼此完全独立，互不知晓对方存在。`main.py` 是唯一的组装点，通过注册 hook 回调将两者桥接。

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: sqlite3 (标准库), openai (已有), 无新增第三方依赖
**Storage**: SQLite, 每 session 一个 {uuid}.db, 存于 .myagent/sessions/
**Testing**: pytest (已有), SessionManager 用 `:memory:` 模式独立可测
**Target Platform**: Windows/Linux CLI
**Project Type**: CLI agent 应用
**Performance Goals**: 单条消息写入 <10ms, session 列表加载 <100ms (≤50 sessions)
**Constraints**: 不修改 Agent.run() 核心循环体（Constitution IX）；Conversation 和 SessionManager 零耦合；compact 与持久化分离
**Scale/Scope**: 单用户，单项目约 10-100 个 session

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Correctness First | ✅ | 新模块独立测试；消息在入口处写入（与 compact 分离）；空对话清理逻辑明确 |
| II | Small Steps | ✅ | P1（持久化+恢复）→ P2（列表管理+UI）→ P3（REPL 内 /resume）。每步独立可测 |
| III | Clarity & Maintainability | ✅ | SessionManager 单一职责（持久化存储），Conversation 单一职责（REPL 编排），两者零耦合 |
| IV | Good Architecture | ✅ | 遵循现有分层：Hook 系统是唯一的桥接点，新模块落点清晰 |
| V | Don't Reinvent | ✅ | sqlite3 标准库；Hook 模式复用项目已有的注册/触发机制 |
| VI | Mainstream Practices | ✅ | Observer 模式（Hook 总线）为业界标准解耦方案 |
| VII | Unit Tests | ✅ | SessionManager 用 `:memory:` 独立可测；Conversation 测试无需感知 SessionManager |
| VIII | Backward Compatibility | ✅ | 新增 hook 事件 `MessageAppended` 为扩展（旧回调不受影响）；Conversation/Agent 零改动 |
| IX | Keep Agent Loop Simple | ✅ | Agent.run() 零改动。持久化通过 hook 事件在外层完成 |
| X | Elevate Design | ✅ | Hook 总线为横切关注点（持久化）提供干净的接入点，不污染核心模块 |

## Project Structure

### Documentation (this feature)

```text
specs/005-session-persistence/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── README.md        # SessionManager interface + Hook events + CLI + UI contracts
├── spec.md              # Feature specification
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
Stage 2/project/
├── main.py                     # [修改] --resume 参数；创建 SessionManager；注册 hook 桥接
├── hooks.py                    # [修改] 新增 MessageAppended 事件
├── agent/
│   ├── session_manager.py      # [新增] SessionManager 类
│   ├── conversation.py         # [修改] /resume 命令处理；触发 SessionStart/End hook
│   └── ui.py                   # [新增] Session 列表交互式 UI（箭头键选择）
├── tools/
│   └── todo_write.py           # [不改动] 持久化由 PostToolUse hook 处理
├── tooling/
│   └── permission/
│       └── engine.py           # [修改] 新增 save_callback: Callable | None 可选参数
└── tests/
    ├── test_session_manager.py        # [新增] SessionManager 测试（:memory:）
    ├── test_session_persistence.py    # [新增] 端到端持久化集成测试
    └── test_conversation.py           # [不改动] Conversation 不知道 SessionManager
```

**Structure Decision**: 单项目结构。`SessionManager` 放在 `agent/` 目录（编排层），交互式 UI 独立为 `agent/ui.py`。所有桥接逻辑集中在 `main.py` 的 hook 注册区。

## Architecture

### 核心设计：Hook 驱动，零耦合

```
                         main.py (唯一组装点)
                        /                    \
           register_hook(...)          register_hook(...)
              /                              \
     Conversation                        SessionManager
     (只做 REPL)                         (只做存储)
         │                                    │
         │  触发 hook 事件                      │  监听 hook 事件
         │  ──────────────────▶  Hook 总线  ──▶  │
         │                                    │
         彼此不知道对方存在                      彼此不知道对方存在
```

### Hook 事件与 SessionManager 的映射

| Hook 事件 | 触发位置 | SessionManager 回调 | 说明 |
|-----------|---------|---------------------|------|
| `SessionStart` | `Conversation.start()` | `create_session()` | REPL 启动时创建 session |
| `MessageAppended` | 消息进入 messages 列表入口处（Conversation 用户输入后、Agent LLM 返回后、ToolExecutor 执行后） | `save_message(msg)` | 每条消息立即持久化，在 compact 修改内存前完成 |
| `PostToolUse` | `ToolExecutor.execute()` 工具执行后 | `save_todos()`（仅 todo_write） | Todo 状态持久化 |
| `SessionEnd` | `Conversation` 退出时 | `cleanup_if_empty()` + `close()` | 空对话清理 + 资源释放 |

### PermissionEngine 的连接

PermissionEngine 不接受 `SessionManager`——它只接受一个 `Callable[[str, str], None]` 类型的 `save_callback`。main.py 负责绑定：

```python
engine = PermissionEngine(save_callback=session_manager.save_permission)
```

### 不变模块

| 模块 | 改动 | 理由 |
|------|------|------|
| `Agent.run()` | **零改动** | 消息持久化由 hook 系统在外层完成 |
| `TodoWriteTool` | **零改动** | Todo 持久化由 `PostToolUse` hook 完成 |
| `ToolExecutor` | **零改动** | 已有 `PostToolUse` 钩子，无需新增持久化逻辑 |
| `Conversation` | 只新增 `/resume` 命令 + `SessionStart`/`SessionEnd` hook 触发 | 职责不变：REPL + 消息编排 |

### 消息持久化时序（关键）

```
① 消息进入 messages 列表
   │
   ├── messages.append(msg)          ← 内存操作
   │
   └── trigger_hooks("MessageAppended", msg)  ← 持久化
          │
          └── SessionManager.save_message(msg)  ← 完整原始消息
                                                  
② Compact 管线（下一轮 LLM 调用前）
   │
   └── compact_pipeline(messages, llm)  ← 原地修改内存副本
                                          不影响已持久化的数据
```

## Detailed Design

### 新增模块

#### `agent/session_manager.py` — SessionManager

- 职责：SQLite 连接管理、消息/Todo/权限 CRUD、session 生命周期
- 构造函数：`__init__(self, sessions_dir: str)` — 接收 `.myagent/sessions/` 绝对路径，自动创建目录
- 对外零依赖——不知道 Conversation、Agent、Hook 系统的存在
- 核心方法见 `contracts/README.md`

#### `agent/ui.py` — Session 列表 UI

- 职责：交互式 session 列表选择（箭头键导航 + 操作选择）
- `select_session(sessions: list[SessionSummary]) -> str | None`
- `confirm_delete(title: str) -> bool`
- `prompt_rename(current_title: str) -> str`
- 标准库实现（Windows: `msvcrt`, Unix: `termios`/`tty`）

### 现有模块改动

#### `hooks.py`

新增事件：`MessageAppended` — 通知型 hook（返回 None），参数为刚追加的 message 对象。在消息被 `messages.append()` 后立即触发。

```python
# hooks.py 新增
MESSAGE_APPENDED = "MessageAppended"
```

#### `main.py`

唯一的组装点。伪代码结构：

```python
# 1. 创建 SessionManager
session_mgr = SessionManager(sessions_dir=".myagent/sessions")

# 2. 处理 --resume
if args.resume:
    sessions = session_mgr.list_sessions()
    if not sessions:
        print("No saved sessions found. Starting new session...")
    else:
        session_id = select_session(sessions)
        if session_id:
            messages = session_mgr.load_messages(session_id)
            permissions = session_mgr.load_permissions(session_id)
            todos = session_mgr.load_todos(session_id)
            # 恢复：messages 注入 Conversation，permissions 注入 engine，todos 注入 TodoWriteTool

# 3. 注册 hook 桥接（SessionManager ↔ 系统）
register_hook("SessionStart", lambda: session_mgr.create_session())
register_hook("MessageAppended", lambda msg: session_mgr.save_message(current_session_id, msg))
register_hook("PostToolUse", make_todo_persister(session_mgr))
register_hook("SessionEnd", lambda: (session_mgr.cleanup_if_empty(), session_mgr.close()))

# 4. 组装并启动
engine = PermissionEngine(save_callback=session_mgr.save_permission)
# ... restore permissions to engine ...
conv = Conversation(agent=agent)
conv.start()
```

#### `agent/conversation.py`

- 新增 `/resume` 命令处理：`_handle_resume()` → 触发保存当前 session → 调 `ui.select_session()` → 切换 messages
- 用户消息入口处：`messages.append(user_msg)` → `trigger_hooks("MessageAppended", user_msg)`
- `start()` 触发 `SessionStart` hook
- 退出时触发 `SessionEnd` hook

#### `agent/agent.py`

- `_execute_tool_calls()` 中 assistant 消息和 tool 消息追加到 messages 后触发 `MessageAppended` hook
- 循环体本身零改动，只在消息追加点加一行 `trigger_hooks`

#### `hook.py`

- 新增 `MESSAGE_APPENDED = "MessageAppended"` 事件常量

#### `tooling/permission/engine.py`

- 新增 `save_callback: Callable[[str, str], None] | None = None` 可选参数
- session 级 allow 时，若 `save_callback` 非 None 则调用

## Complexity Tracking

> 无 Constitution 违规，无需记录。
