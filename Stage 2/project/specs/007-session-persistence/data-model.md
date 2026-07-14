# Data Model: Session 持久化

**Feature**: 007-session-persistence
**Date**: 2026-07-14

## Entity Overview

```
┌──────────────────┐      ┌──────────────────┐
│    Session       │ 1──* │    Message       │
│  (sessions)      │      │  (messages)      │
├──────────────────┤      ├──────────────────┤
│ id: TEXT PK      │      │ id: TEXT PK      │
│ updated_at: TEXT │      │ session_id: TEXT │──┐
│ title: TEXT      │      │ seq: INTEGER     │  │
│ message_count:   │      │ role: TEXT       │  │
│   INTEGER        │      │ content: TEXT    │  │
└──────────────────┘      │ tool_calls: TEXT │  │
                          │ tool_call_id:    │  │
                          │   TEXT           │  │
                          └──────────────────┘  │
                                                 │
┌──────────────────┐      ┌──────────────────┐   │
│  PermissionGrant │      │      Todo        │   │
│  (permissions)   │      │    (todos)       │   │
├──────────────────┤      ├──────────────────┤   │
│ session_id: TEXT │──────│ session_id: TEXT │───┘
│ tool_name: TEXT  │      │ position: INTEGER│
│ rule_content:    │      │ content: TEXT    │
│   TEXT           │      │ status: TEXT     │
│                  │      │ active_form: TEXT│
└──────────────────┘      └──────────────────┘
```

## 1. Session（持久化表: `sessions`）

代表一次完整的 Agent 会话，存于 SQLite 的 `sessions` 表。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | TEXT | PRIMARY KEY | UUID4，如 `a1b2c3d4-...` |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC 时间戳（Python 生成） |
| `title` | TEXT | NOT NULL | 首条 user 消息截取前 50 字符；无 user 消息则为 `"Untitled"` |
| `message_count` | INTEGER | NOT NULL | 消息总数（含 system），每写一条消息递增 |

**状态转换**：
```
[创建] → updated_at = now(), title = "Untitled", message_count = 1 (system)
      → [每写一条消息] → updated_at = now(), message_count += 1
      → [首条 user 消息] → title = user_content[:50]
      → [退出 · 空对话] → DELETE (文件删除)
      → [退出 · 有对话] → 保留 (不修改)
```

**唯一性**: `id` 全局唯一（UUID4 生成）。

## 2. Message（持久化表: `messages`）

对话中的一条消息。按 `seq` 排序可重建完整对话链。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | TEXT | PRIMARY KEY | UUID4 |
| `session_id` | TEXT | NOT NULL, FK→sessions(id) | 所属 session |
| `seq` | INTEGER | NOT NULL, UNIQUE(session_id, seq) | 递增序号，从 0 开始（system=0） |
| `role` | TEXT | NOT NULL | `system` / `user` / `assistant` / `tool` |
| `content` | TEXT | NULL | 消息正文（JSON 序列化）；tool 消息存 `json.dumps(result)` |
| `tool_calls` | TEXT | NULL | assistant 消息的 tool_calls JSON；无工具调用则为 NULL |
| `tool_call_id` | TEXT | NULL | tool 消息的 tool_call_id；非 tool 消息为 NULL |

**设计约束**:
- `tool_name` 不在本表中冗余——恢复时从前置 assistant 消息的 `tool_calls` JSON 临时推导
- `(session_id, seq)` 唯一约束保证 seq 不重复
- `content` 和 `tool_calls` 以 JSON 字符串存储（TEXT 类型）
- 外键 `ON DELETE CASCADE`：删除 session 时自动清除消息

**消息字段契约**（恢复后统一满足）:
```python
# 允许的字段（与 OpenAI 兼容接口一致）
{"role": str, "content": str | None, "tool_calls": list | None, "tool_call_id": str | None}
# 不允许的字段
# tool_name —— 不存在！恢复时从 assistant 消息推导
# SDK object —— 不存在！统一为 dict
```

**seq 分配**: 使用 `BEGIN IMMEDIATE` + `MAX(seq) + 1`，同一事务内完成。

## 3. PermissionGrant（持久化表: `permissions`）

用户在某 session 中授予的一条精确权限。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `session_id` | TEXT | NOT NULL, FK→sessions(id) | 所属 session |
| `tool_name` | TEXT | NOT NULL | 工具名称 |
| `rule_content` | TEXT | NOT NULL | 精确的策略规则描述文本 |

**主键**: `(session_id, tool_name, rule_content)` —— 联合主键，自动去重

**值对象**（Python，非持久化）:
```python
@dataclass(frozen=True)
class PermissionGrant:
    tool_name: str
    rule_content: str
```

**语义**:
- 在表中存在 = 用户已 "始终允许" 该规则
- 不持久化 deny 决策
- 恢复时若 `rule_content` 在策略文件中无对应规则 → 跳过并警告
- 外键 `ON DELETE CASCADE`：删除 session 时自动清除

## 4. Todo（持久化表: `todos`）

Agent 通过 TodoWrite 工具维护的任务列表。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `session_id` | TEXT | NOT NULL, FK→sessions(id) | 所属 session |
| `position` | INTEGER | NOT NULL | 列表位置序号（0-based），保留顺序 |
| `content` | TEXT | NOT NULL | 任务描述 |
| `status` | TEXT | NOT NULL | `pending` / `in_progress` / `completed` |
| `active_form` | TEXT | NOT NULL | 进行中的显示文案 |

**主键**: `(session_id, position)` —— 使用 `position` 而非 `content`，允许相同描述出现在不同位置

**与 TodoWriteTool 的桥接**:
```
TodoWriteTool.run(params)
  → CURRENT_TODOS.clear(); CURRENT_TODOS.extend(todos)
  → PostToolUse hook: save_todos(session_id, snapshot_todos())
  → 内存: CURRENT_TODOS (模块级列表)
  → 持久化: todos 表
```

## 5. ActiveSession（运行时 dataclass，`agent/session_controller.py`）

不持久化。SessionController 持有的唯一活跃会话。属于应用层状态，不属于 Repository。

```python
@dataclass
class ActiveSession:
    id: str           # session UUID
    title: str         # 当前标题
    messages: list[dict]  # working context（可被 compact 原地修改）
```

**生命周期**: `start_new()` 或 `resume()` 创建 → `close()` 销毁

**位置**: 与 SessionController 同文件（`agent/session_controller.py`），不在 SessionManager 中。

## 6. SessionSnapshot（恢复快照，`agent/session_manager.py`）

不直接持久化，是 Repository 的查询结果聚合。

```python
@dataclass
class SessionSnapshot:
    id: str
    title: str
    updated_at: str
    message_count: int
    messages: list[dict]                  # 按 seq 排序，每个 dict 仅含 4 字段
    permissions: list[PermissionGrant]    # 显式类型，非 list[dict]
    todos: list[dict]                     # 按 position 排序，含 position/status/active_form
```

**事务保证**: `load_session()` 在同一个只读连接和事务内读取全部状态，确保不拿到跨时点半份数据。

## 7. SessionSummary（列表展示）

不持久化。列表查询的轻量投影。

```python
@dataclass
class SessionSummary:
    id: str            # UUID
    title: str         # 截取后的标题
    updated_at: str    # ISO 时间戳
    message_count: int # 消息总数
```

**查询**: `SELECT id, updated_at, title, message_count FROM sessions ORDER BY updated_at DESC`

## Schema DDL

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
