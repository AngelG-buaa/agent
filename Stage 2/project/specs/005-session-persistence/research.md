# Research: Session 持久化

**Feature**: 005-session-persistence
**Date**: 2026-07-13

## 1. 存储格式选型：SQLite per-session

**Decision**: SQLite，每 session 一个独立 `.db` 文件

**Rationale**:
- Python 内置 `sqlite3` 模块，零额外依赖（Constitution V）
- Session 管理操作（列表、搜索、重命名、删除）需要结构化查询——SQL 天然优势
- 每 session 独立文件：删除 = `os.remove()`，天然隔离，无需 VACUUM
- WAL 模式支持读写并发（同一 session 不会被两个进程同时操作，但安全起见启用）
- 与 JSONL 对比：myAgent 对话为线性消息序列（无 parallel tool results 修复、无 snip 链路修补、无 sidechain），不需要 JSONL 的图结构恢复能力，SQLite 的查询和元数据管理优势更匹配需求场景

**Alternatives Considered**:
- **JSONL** (Claude Code 方案): 写入简单但需要尾部重挂 metadata、大文件增量读取、恢复时链路修复。Claude Code 选 JSONL 是因为其复杂的消息图结构（parallel tools、snip、sidechain、remote ingress），myAgent 不需要这些
- **单文件 SQLite（所有 session 共享）**: 跨 session 查询方便但删除需 DELETE 多表，不与 `os.remove()` 对齐
- **JSON 文件**: 大文件全量读写，不支持增量，不适合恢复场景

## 2. 消息持久化时机：入口处立即写入 vs PostRound

**Decision**: 消息进入 messages 列表的入口处立即持久化

**Rationale**:
- Context Compact（L1/L2/L3/L4）会在每轮 loop 前**原地修改** messages 列表
- 如果在 PostRound 写入，持久化的是 compact 压缩后的"残片"而非原始完整对话
- 入口处写入确保持久化的是原始消息，compact 修改的是内存副本——两条线分离
- 写入点：user 消息（Conversation 收到用户输入后）、assistant 消息（Agent.run() LLM 返回后）、tool 消息（ToolExecutor 执行后）

**Alternatives Considered**:
- **PostRound hook 写入**: 与 compact 冲突，持久化压缩残片
- **退出时全量写入**: 崩溃丢数据，不符合持久化目标

## 3. 交互式 Session 列表 UI

**Decision**: 箭头键导航 + 回车选择的交互式菜单，纯标准库实现

**Rationale**:
- 用户选择 Option C（交互式选择），用户体验优于编号菜单
- Constitution V：标准库能做的事不引入第三方库
- Windows 实现：`msvcrt.getch()` 捕获方向键（`\xe0` 前缀 + `H`/`P`/`K`/`M`）
- Unix 实现：`termios` + `tty` + `sys.stdin.read()` 捕获 ANSI escape 序列
- 选中 session 后弹出操作选项：`[R]esume / [D]elete / [R]ename`

**Alternatives Considered**:
- **questionary/inquirer 第三方库**: 违反 Constitution V
- **纯编号菜单**: 用户否决（Option A）
- **curses 全屏 TUI**: 过重，会话列表只需单列选择

## 4. Schema 3NF 分析

**Decision**: 四表设计，所有表满足 3NF

**Rationale**:
- `tool_name` 不在 messages 表中冗余：恢复时从 assistant 消息的 `tool_calls` JSON 反向查找（`tool_call_id` → `tool_calls[*].function.name`），消除传递依赖 `id → tool_call_id → tool_name`
- `parent_id` 不需要：消息为线性序列，`seq` 完全确定顺序
- `timestamp` 不需要：消息级时间戳对恢复无价值，session 级 `updated_at` 足够
- 3NF 验证见 `data-model.md`

## 5. Session 生命周期：SessionStart/SessionEnd hook 驱动

**Decision**: `Conversation.start()` 触发 `SessionStart` hook → `SessionManager.create_session()`；退出时触发 `SessionEnd` hook → `SessionManager.cleanup_if_empty()` + `close()`

**Rationale**:
- SessionManager 和 Conversation 完全独立——Conversation 只负责触发 hook，不知道谁在监听
- 创建时机在 REPL 启动时（`Conversation.start()`），比 `main.py` 解析参数时更晚、比第一条用户消息更早——无论用户是否输入都创建
- 空对话判定：`SELECT COUNT(*) FROM messages WHERE role = 'user'` = 0（无用户消息即为空对话）
- 清理逻辑封装在 `SessionManager` 内部，Conversation 不感知持久化
- `--resume` 时无历史 session：提示 "No saved sessions found." 后自动进入新 session

## 6. 权限持久化：只记 allow

**Decision**: permissions 表只存储 allow 决策，不存储 deny

**Rationale**:
- 在表中 = 已允许，不在表中 = 需重新确认
- `INSERT OR IGNORE` 利用 PRIMARY KEY 约束天然防重复
- 权限不跨 session 共享：每个 session 的 permissions 表独立

## 7. Todo 持久化：PostToolUse Hook

**Decision**: 通过 PostToolUse hook 监听 todo_write 工具调用，由 main.py 注册回调 `session_manager.save_todos()`

**Rationale**:
- TodoWriteTool 零改动——不知道持久化的存在
- PostToolUse 在工具执行后触发，此时 todo 状态已更新
- 与架构理念一致：持久化是横切关注点，通过 hook 系统注入
- 先 DELETE 旧记录再 INSERT 新列表，保证与内存状态一致

**Alternatives Considered**:
- **TodoWriteTool 内部直接调用**: 会导致 TodoWriteTool 依赖 SessionManager，引入耦合

## 8. 架构集成模式：Hook 驱动（非构造注入）

**Decision**: SessionManager 和 Conversation 完全独立，通过 Hook 系统桥接。main.py 是唯一的组装点

**Rationale**:
- 持久化是横切关注点（cross-cutting concern），不应污染任何核心模块的职责
- Hook 总线是项目已有的解耦机制——所有新模块都应优先通过它接入
- 比较三种方案的耦合度：
  - 构造注入：Conversation → SessionManager 直接依赖，Agent/ToolExecutor/TodoWriteTool 也需要感知
  - on_message 回调：Conversation → SessionManager 直接依赖，Agent 多了回调参数
  - **Hook 驱动**：Conversation 不知道 SessionManager 存在，SessionManager 不知道 Conversation 存在。只有 hook 事件字符串是共享的
- 新的 `MessageAppended` hook 事件填补了"消息追加"这一现有 hook 体系未覆盖的时机点，后续其他扩展（如审计日志、远程同步）也可复用

**Alternatives Considered**:
- **构造注入**: 违反横切关注点隔离原则，导致模块间不必要的耦合
- **on_message 回调**: 比构造注入好，但仍然让 Conversation 持有 SessionManager 引用
- **全局单例**: 违反 Constitution IV（避免全局单例），且不可测试
