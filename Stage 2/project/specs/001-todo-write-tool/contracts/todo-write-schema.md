# Contract: todo_write Tool Schema

**Feature**: TodoWrite Tool | **Date**: 2026-07-11

## Tool Definition

```json
{
  "type": "function",
  "function": {
    "name": "todo_write",
    "description": "Use this tool to create and manage a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness. This tool does not perform any actual work — it only manages the task list.",
    "parameters": {
      "type": "object",
      "properties": {
        "todos": {
          "type": "array",
          "description": "The complete list of tasks. Each task has a content and status.",
          "items": {
            "type": "object",
            "properties": {
              "content": {
                "type": "string",
                "description": "Description of the task"
              },
              "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "Current status: pending (not started), in_progress (working on it), completed (done)"
              }
            },
            "required": ["content", "status"]
          }
        }
      },
      "required": ["todos"]
    }
  }
}
```

## Input Contract

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `todos` | array | ✅ | 可为空数组 `[]` |
| `todos[].content` | string | ✅ | 非空，描述任务的文本 |
| `todos[].status` | string | ✅ | 必须为 `pending` \| `in_progress` \| `completed` |

## Output Contract

### Success

```json
{
  "result": "Updated N tasks"
}
```

Where `N` is `len(todos)`.

### Error — Invalid Status

```json
{
  "error": "Invalid status: 'xxx'. Must be one of: pending, in_progress, completed"
}
```

### Error — Empty Content

```json
{
  "error": "Task content cannot be empty"
}
```

## Display Format

调用成功后，终端输出以下格式（非 JSON 返回，是 print side-effect）：

```
## Current Tasks
  [ ] Task description for pending task
  [▸] Task description for in-progress task
  [✓] Task description for completed task
```

- `[ ]` = pending
- `[▸]` = in_progress
- `[✓]` = completed
