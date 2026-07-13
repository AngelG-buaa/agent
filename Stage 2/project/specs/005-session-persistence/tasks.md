# Tasks: Session 持久化

**Input**: Design documents from `specs/005-session-persistence/`

**Prerequisites**: plan.md, spec.md (user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Constitution Principle VII — Agent 循环逻辑、Tool 执行管线、权限引擎为"核心模块"，测试强制。SessionManager 属于 agent 编排层（核心模块），必须有测试覆盖。

**Organization**: Tasks grouped by user story (US1/US2/US3/US4)，每个 story 独立可测。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属 user story（US1, US2, US3, US4）
- 每条含确切的文件路径

---

## Phase 1: Setup

**Purpose**: 建立基线，确认无回归

- [ ] T001 运行现有全部测试，确认通过：`D:/Miniconda/envs/llm/python -m pytest tests/ -q`

---

## Phase 2: Foundational — SessionManager + Hook 基础

**Purpose**: SessionManager 是实现所有 US 的前置依赖。必须在本阶段完成后才能开始任何 US 工作。

**⚠️ CRITICAL**: T002–T006 为并行基础任务，T007–T009 依赖 T002 完成，T010 依赖 T007–T009。

### Hook 事件扩展

- [ ] T002 在 `hooks.py` 中新增 `MessageAppended` 事件常量（`MESSAGE_APPENDED = "MessageAppended"`），通知型 hook，参数为刚追加的 message 对象。文件：`hooks.py`

### SessionManager 实现

- [ ] T003 [P] 创建 `SessionManager` 类骨架：`__init__(self, sessions_dir: str)`，自动创建 sessions_dir 目录（`os.makedirs(exist_ok=True)`），保存 sessions_dir 属性。数据库连接通过 `@contextmanager` 管理（`get_connection(session_id: str)` → `sqlite3.connect(path)`）。**所有 SQL 操作必须使用参数化查询（`?` 占位符），禁止字符串拼接**（FR-015）。文件：`agent/session_manager.py`
- [ ] T004 [P] 实现 `SessionSummary` dataclass：`id: str`, `title: str`, `updated_at: str`, `message_count: int`。文件：`agent/session_manager.py`
- [ ] T005 [P] 实现 schema 初始化方法 `_init_schema(conn)`：CREATE TABLE IF NOT EXISTS 四张表（sessions, messages, permissions, todos），messages 表创建复合索引 `idx_messages_session_seq ON messages(session_id, seq)`。文件：`agent/session_manager.py`
- [ ] T006 实现 session CRUD 方法：`create_session() -> str`（UUID + 创建 .db + 初始化 schema + INSERT sessions + 返回 uuid）、`list_sessions() -> list[SessionSummary]`（遍历 *.db → 读 sessions 表 → 按 updated_at 降序 → 跳过损坏文件打印警告，FR-016）、`delete_session(session_id)`（`os.remove`）、`rename_session(session_id, new_title)`（UPDATE sessions SET title）。**依赖 T003（需要 get_connection context manager）**。文件：`agent/session_manager.py`

### SessionManager 核心方法（依赖骨架 T003）

- [ ] T007 实现消息持久化方法：`save_message(session_id, message: dict)`（用 `get_role/get_content/get_tool_calls/get_tool_call_id` 提取字段 → INSERT INTO messages → UPDATE sessions SET updated_at, message_count）。**当 role='user' 且当前 title='Untitled' 时，自动截取 content 前 50 字符 UPDATE sessions SET title**（FR-012）。Raises sqlite3.Error on failure。文件：`agent/session_manager.py`
- [ ] T008 实现消息加载方法：`load_messages(session_id) -> list[dict]`（SELECT * ORDER BY seq → 对 tool 消息通过 tool_call_id 从前方 assistant 消息的 tool_calls JSON 反查 tool_name 并注入 → 返回纯 dict 列表）。文件：`agent/session_manager.py`
- [ ] T009 [P] 实现权限持久化方法：`save_permission(session_id, tool_name)`（INSERT OR IGNORE）、`load_permissions(session_id) -> set[str]`（SELECT tool_name → 返回 set）。文件：`agent/session_manager.py`
- [ ] T010 [P] 实现 Todo 持久化方法：`save_todos(session_id, todos: list[dict])`（DELETE 该 session 全部旧记录 → INSERT 新列表）、`load_todos(session_id) -> list[dict]`（SELECT *）。文件：`agent/session_manager.py`
- [ ] T011 实现生命周期方法：`cleanup_if_empty(session_id)`（`SELECT COUNT(*) FROM messages WHERE role = 'user'` = 0 → `os.remove` → Warning log）、`close()`（关闭所有连接）。文件：`agent/session_manager.py`

### PermissionEngine 扩展

- [ ] T012 为 `PermissionEngine.__init__` 新增可选参数 `save_callback: Callable[[str, str], None] | None = None`，session 级 allow 时若回调非 None 则调用 `save_callback(session_id, tool_name)`。文件：`tooling/permission/engine.py`

### 测试（核心模块，强制）

- [ ] T013 编写 SessionManager 单元测试：用 `sessions_dir=tempfile.mkdtemp()` 或 `:memory:` 模式，覆盖 create/list/delete/rename/save_message/load_messages/save_permission/load_permissions/save_todos/load_todos/cleanup_if_empty/close 全部方法。**依赖 T007–T012 全部完成（需要完整方法签名和实现）**。文件：`tests/test_session_manager.py`

**Checkpoint**: `python -m pytest tests/test_session_manager.py -q` 全部通过。SessionManager 可独立使用，Hook 事件已定义，PermissionEngine 已扩展。

---

## Phase 3: User Story 1 — 会话自动持久化与退出恢复 (Priority: P1) 🎯 MVP

**Goal**: 对话内容自动保存，`--resume` 恢复。恢复后消息、权限、Todo 均可用。

**Independent Test**: 启动 → 对话 → 退出 → `python main.py --resume` → 选择 session → 继续对话 → Agent 理解上下文。

### Hook 桥接

- [ ] T014 [US1] 在 `Conversation.start()` 中触发 `SessionStart` hook（在 REPL 循环开始前，`trigger_hooks("SessionStart")`）。文件：`agent/conversation.py`
- [ ] T015 [P] [US1] 在 `Conversation` 收到用户输入后（`messages.append(user_msg)` 之后），触发 `trigger_hooks("MessageAppended", user_msg)`。文件：`agent/conversation.py`
- [ ] T016 [P] [US1] 在 `Agent._execute_tool_calls()` 中，assistant 消息追加到 messages 后触发 `trigger_hooks("MessageAppended", assistant_msg)`，tool 消息追加后触发 `trigger_hooks("MessageAppended", tool_msg)`。文件：`agent/agent.py`
- [ ] T017 [P] [US1] 在 main.py 中注册 hook 回调：`SessionStart` → `session_mgr.create_session()`，`MessageAppended` → `session_mgr.save_message(current_session_id, msg)`。文件：`main.py`

### CLI --resume 入口

- [ ] T018 [US1] 在 `main.py` 新增 `--resume` CLI 参数（`argparse.add_argument("--resume", action="store_true")`）。`--resume` 时：调 `session_mgr.list_sessions()` → 若空则 print "No saved sessions found. Starting new session..." 并进入新 session →若非空则展示交互式选择 → 选中后 `load_messages/load_permissions/load_todos` → 恢复权限到 engine → 恢复 messages 到 Conversation → 进入 REPL。文件：`main.py`

### Todo 持久化

- [ ] T019 [US1] 在 main.py 中注册 `PostToolUse` hook 回调：检测 `tool_name == "todo_write"` → 从 tool_params 提取 todos → `session_mgr.save_todos(session_id, todos)`。文件：`main.py`

### Session 退出处理

- [ ] T020 [US1] 在 `Conversation` 退出路径（`/exit` + Ctrl+C）中触发 `SessionEnd` hook（`trigger_hooks("SessionEnd")`）。文件：`agent/conversation.py`
- [ ] T021 [P] [US1] 在 main.py 中注册 `SessionEnd` hook 回调：`session_mgr.cleanup_if_empty(current_session_id)` + `session_mgr.close()`。文件：`main.py`

### 测试

- [ ] T022 [US1] 编写端到端持久化集成测试：覆盖新建 session → save_message → 退出 → load_messages 恢复 → 消息完整无缺；空对话退出 → db 文件被清理；compact 后退出 → 恢复的为原始消息。用 tempfile + :memory: 模式避免文件系统污染。文件：`tests/test_session_persistence.py`

**Checkpoint**: `python main.py` 对话后退出 `.db` 存在；`python main.py --resume` 可恢复并继续对话；空对话自动清理。

---

## Phase 4: User Story 2 — Session 列表管理与操作 (Priority: P2)

**Goal**: `--resume` 展示交互式 session 列表，支持恢复、删除（确认后）、重命名。

**Independent Test**: 创建 3 个 session → `--resume` → 列表显示 3 个条目 → 删除 1 个 → 剩 2 个 → 重命名 → 验证标题更新。

### 交互式 UI

- [ ] T023 [US2] 创建 `select_session(sessions: list[SessionSummary]) -> str | None` 函数：渲染列表（箭头键 ↑↓ 导航，当前行高亮 `>`），Enter 选中返回 session_id，Q 返回 None。Windows: `msvcrt.getch()`，Unix: `termios`+`tty`+`sys.stdin.read()`。文件：`agent/ui.py`
- [ ] T024 [P] [US2] 创建 `confirm_delete(title: str) -> bool` 函数：打印 "Delete session '{title}'? Are you sure? [y/N]"，返回 True/False。文件：`agent/ui.py`
- [ ] T025 [P] [US2] 创建 `prompt_rename(current_title: str) -> str` 函数：打印当前标题 → `input("New title: ")` → 返回新标题（允许空 = 不变）。文件：`agent/ui.py`

### 集成到 --resume

- [ ] T026 [US2] 扩展 `--resume` 流程：列表显示后，选中 session 弹出操作选择 `[R]esume / [D]elete / [R]ename` → D 调 `confirm_delete` → 确认后 `delete_session`；R 调 `prompt_rename` → `rename_session` → 刷新列表。文件：`main.py`

**Checkpoint**: `python main.py --resume` 可箭头键浏览/选择/删除/重命名 session。

---

## Phase 5: User Story 3 — REPL 内会话切换 (Priority: P3)

**Goal**: REPL 中输入 `/resume`，先保存当前 session，展示列表供切换。

**Independent Test**: Session A 对话 3 轮 → `/resume` → 切换到 Session B → `/resume` → 切回 Session A → 3 轮对话完整保留。

**Architecture**: Conversation 不 import SessionManager。Conversation 只负责：(1) 暴露 `resume_session(messages, permissions, todos)` 公共方法接收预加载的数据，(2) 拦截 `/resume` 命令触发 hook。main.py 的 hook 回调负责协调 SessionManager + UI + Conversation。

- [ ] T027 [US3] 在 `Conversation` 中新增 `resume_session(self, messages: list[dict], permissions: set[str], todos: list[dict], title: str)` 公共方法：用参数替换 `self.messages`，恢复 todo 状态到 TodoWriteTool，print "Resumed session: {title}"。文件：`agent/conversation.py`
- [ ] T028 [US3] 在 `Conversation.start()` 的用户输入处理中新增 `/resume` 命令拦截（在检查 `/exit` 之后），触发 `trigger_hooks("ResumeRequested")`，hook 返回非 None 时表示已处理（main.py 回调中完成切换后返回 True）。文件：`agent/conversation.py`
- [ ] T028a [P] [US3] 在 `hooks.py` 中新增 `ResumeRequested` 事件常量（`RESUME_REQUESTED = "ResumeRequested"`），控制型 hook（返回值非 None 时中断链路）。文件：`hooks.py`
- [ ] T028b [US3] 在 main.py 中注册 `ResumeRequested` hook 回调：触发 `SessionEnd`（保存当前）→ 调 `ui.select_session(session_mgr.list_sessions())` → 若选中则 `session_mgr.load_messages/load_permissions/load_todos` → `conv.resume_session(...)` → 返回 True。文件：`main.py`

**Checkpoint**: REPL 中输入 `/resume` 可切换 session，切出再切回不丢数据。Conversation 零 SessionManager 依赖。

---

## Phase 6: User Story 4 — 权限跨轮持久化 (Priority: P3)

**Goal**: session 级 allow 的工具，后续轮次和 resume 后自动放行。

**Independent Test**: Session 中 allow bash → 再次触发 bash → 不弹确认。退出 resume → 触发 bash → 不弹确认。

- [ ] T029 [US4] 在 main.py 组装时绑定 `PermissionEngine(save_callback=session_mgr.save_permission)`：`--resume` 恢复时将 `load_permissions()` 结果注入 engine（遍历 set 对每个 tool_name 调用 engine 的内部设为 allowed）。文件：`main.py`

**Checkpoint**: allow 后不弹确认，resume 后也不弹。权限不跨 session 共享（切换到另一个 session 后需重新确认）。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 验证、清理、文档

- [ ] T030 运行全部测试（旧 + 新），确认无回归：`D:/Miniconda/envs/llm/python -m pytest tests/ -q`
- [ ] T031 [P] 按 `quickstart.md` 执行全部 6 个 validation scenario，确认通过
- [ ] T032 [P] 更新 `README.md`：在"运行"节新增 `--resume` 用法说明，在"核心概念"节新增 Session 持久化条目（1-2 句概要 + 指向 spec 的链接）。文件：`README.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup → **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Foundational → MVP
- **US2 (Phase 4)**: Depends on Foundational（可独立于 US1，但 UI 需 `SessionSummary`）→ 建议在 US1 后做（因为 `--resume` 入口在 US1 建立）
- **US3 (Phase 5)**: Depends on US1 + US2（需要 `--resume` 入口 + UI）
- **US4 (Phase 6)**: Depends on Foundational（独立于 US1/US2/US3）
- **Polish (Phase 7)**: Depends on all desired user stories

### User Story Dependencies

```
Foundational (Phase 2)
    │
    ├── US1 (Phase 3) ──┬── US3 (Phase 5)
    │                    │
    ├── US2 (Phase 4) ──┘
    │
    └── US4 (Phase 6) ── 独立，与 US1/US2/US3 无依赖
```

- **US1 (P1)**: 依赖 Foundational。US2 建议在 US1 后（共享 `--resume` 入口）
- **US2 (P2)**: 可独立于 US1 开始（仅需 `SessionSummary` + `SessionManager` 方法），但列表入口 `--resume` 在 US1 中建立，实际开发建议 US1→US2 顺序
- **US3 (P3)**: 依赖 US1（需要 session 切换基础设施）和 US2（需要 `select_session` UI）
- **US4 (P3)**: 依赖 Foundational，与 US1/US2/US3 无直接依赖，可并行

### Within Each Phase

- T003–T006 可并行（SessionManager 骨架 + 不同方法组）
- T007–T011 依赖 T003（骨架），T007–T010 之间可并行
- T013 可在 T007–T012 全部完成后开始（需要完整方法签名）
- T014–T017 可并行（不同文件的 hook 触发点）

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Phase 1: Setup — 确认基线
2. Phase 2: Foundational — SessionManager + Hook
3. Phase 3: US1 — 持久化 + --resume
4. Phase 4: US2 — 交互式列表管理
5. **STOP and VALIDATE**: 已有完整的 session 管理能力（新建/恢复/删除/重命名）
6. Phase 5–7: US3 + US4 + Polish

### Incremental Delivery

1. Setup + Foundational → SessionManager 可用
2. + US1 → 可持久化、可恢复（最小可用版本）
3. + US2 → 可浏览/管理历史 session
4. + US3 → REPL 内切换
5. + US4 → 权限跨轮保持
6. + Polish → 验证 + 文档

---

## Parallel Example: Phase 2 Foundational

```bash
# 并行启动 T002–T005（T002 独立，T003/T004/T005 之间无依赖）：
Task: "T002 新增 MessageAppended event in hooks.py"
Task: "T003 创建 SessionManager 类骨架 in agent/session_manager.py"
Task: "T004 实现 SessionSummary dataclass in agent/session_manager.py"
Task: "T005 实现 schema 初始化 in agent/session_manager.py"

# T003 完成后启动 T006（CRUD 依赖 get_connection context manager）：
Task: "T006 实现 session CRUD in agent/session_manager.py"

# T003 + T006 完成后并行启动 T007–T010（方法实现互不依赖）：
Task: "T007 实现 save_message（含 auto-title）in agent/session_manager.py"
Task: "T008 实现 load_messages in agent/session_manager.py"
Task: "T009 实现 save/load_permissions in agent/session_manager.py"
Task: "T010 实现 save/load_todos in agent/session_manager.py"

# T006 完成后启动 T011 + T012（并行）：
Task: "T011 实现 cleanup_if_empty + close in agent/session_manager.py"
Task: "T012 PermissionEngine save_callback in tooling/permission/engine.py"

# T007–T012 全部完成后：
Task: "T013 SessionManager 单元测试 in tests/test_session_manager.py"
```

## Parallel Example: Phase 3 US1

```bash
# Hook 触发点可在不同文件中并行：
Task: "T014 触发 SessionStart hook in agent/conversation.py"
Task: "T015 触发 MessageAppended hook (user msg) in agent/conversation.py"
Task: "T016 触发 MessageAppended hook (assistant/tool msg) in agent/agent.py"
Task: "T017 注册 hook 回调 in main.py"
```
