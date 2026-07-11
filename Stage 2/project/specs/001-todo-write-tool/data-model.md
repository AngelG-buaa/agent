# Data Model: TodoWrite Tool

**Feature**: TodoWrite Tool | **Date**: 2026-07-11

## Entity: TodoItem

单个任务项。

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | ✅ | 任务描述 |
| `status` | enum | ✅ | `pending` \| `in_progress` \| `completed` |

### Validation Rules

- `content`: 非空字符串。空串应拒绝。
- `status`: 必须为 `pending`、`in_progress`、`completed` 之一。非法值应拒绝并返回明确错误信息。

### State Transitions

```
pending ──→ in_progress ──→ completed
  │                              │
  └──────────────────────────────┘  (可直接标记为 completed)
```

无反向转换限制——Agent 可以将任意状态改为任意合法值（包括 completed → pending 的"重新打开"场景）。

## Entity: TodoList

当前所有任务的完整快照。

- **Type**: `list[TodoItem]`，存储在进程内存中（全局变量 `CURRENT_TODOS`）
- **Lifecycle**: 每次 `todo_write` 调用整体替换。不持久化，进程退出后消失。
- **Identity**: 无唯一 ID，按列表顺序排列。同一 content 可出现多次（不同位置）。
- **Empty State**: 空列表合法，表示无待办任务。

## Entity: RoundCounter

记录 Agent 连续未调用 todo_write 的轮数，用于触发提醒。

- **Type**: `int`，初始值 0
- **Increment Rule**: Agent 主循环每轮结束后，若该轮未调用 todo_write，则 +1
- **Reset Rule**: 当 Agent 调用 todo_write 时重置为 0；当计数器达到 3 并注入提醒后也重置为 0
- **Trigger**: 计数器 == 3 → 注入提醒 → 重置为 0（若 Agent 持续忽略提醒，3 轮后会再次触发）

### State Diagram

```
   0 ──(round w/o todo_write)──→ 1
   1 ──(round w/o todo_write)──→ 2
   2 ──(round w/o todo_write)──→ 3 ──→ inject reminder ──→ 0
   any ──(todo_write called)───→ 0
```

## Relationships

```
Agent.run() 循环
  ├── 持有 RoundCounter
  ├── 每轮检查 counter == 3 → 注入提醒
  └── 调用 ToolExecutor.execute()
        └── 可能调用 TodoWriteTool.run()
              ├── 验证 TodoItem[] 合法性
              ├── 更新全局 TodoList
              └── 打印可视化输出
```
