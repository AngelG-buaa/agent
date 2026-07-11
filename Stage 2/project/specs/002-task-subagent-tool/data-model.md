# Data Model: Task Tool & Sub-Agent

**Feature**: 002-task-subagent-tool
**Date**: 2026-07-11 (updated)

## Entity: TaskTool

继承自 `Tool` 基类，是 LLM 可见的 `task` 工具的代码实现。

| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 固定值 `"task"` |
| `description` | `str` | 工具描述，告知 LLM 何时使用（委派子任务） |
| `_llm` | `LLMClient \| None` | 通过 `set_context()` 注入 |
| `_executor` | `ToolExecutor \| None` | 通过 `set_context()` 注入 |

**参数 (input schema)**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | `string` | ✅ | 子任务的自然语言描述 |

**生命周期**:
1. `tools/__init__.py`: `executor.register(TaskTool())` — 注册时 llm/executor 为 None
2. `main.py`: `task_tool.set_context(llm, executor)` — 注入运行时依赖
3. LLM 调用 `task(description="...")` → `ToolExecutor.execute("task", params)` → `TaskTool.run(params)`
4. `run()` 调用 `spawn_subagent(description, self._llm, self._executor)`
5. 结果以角色 `"tool"` 追加到主 Agent 消息列表

---

## Entity: SubAgent(Agent) 子类

`SubAgent` 是 `Agent` 的子类（定义在 `agent/agent.py`），将 Sub-Agent 的配置和特殊行为封装在类内部。

| 属性 | 值来源 | 说明 |
|------|--------|------|
| `llm` | 主 Agent 的 `LLMClient` | 共享 LLM 配置 |
| `executor` | 主 Agent 的 `ToolExecutor` | 共享 permission session |
| `system_prompt` | `SUB_SYSTEM_PROMPT`（`__init__` 中硬编码） | 独立精简 prompt |
| `max_steps` | `30`（`__init__` 中硬编码） | Sub-Agent 终止限制 |
| `tool_filter` | `{"task", "todo_write"}`（`__init__` 中硬编码） | 排除两个工具 |
| `print_handler` | `sub_print_handler`（`__init__` 中硬编码） | `[sub]` 精简格式 |
| `_round` | `0` → 每轮 +1 | 实例变量，跟踪工具调用轮数 |

**轮数跟踪与提醒注入**（`_execute_tool_calls` 覆盖）:
1. 每轮调用 `self._round += 1`
2. 当 `self._round == 30`：先向 messages 注入提醒 → 再调用 `super()._execute_tool_calls()`
3. Agent 循环在 `max_steps=30` 硬截断保证终止

---

## Entity: Sub-Agent Tool Set

通过 `tool_filter={"task", "todo_write"}` 从主 Agent 的 11 个工具中排除 2 个。

| 工具 | Sub-Agent 可用 | 说明 |
|------|:---:|------|
| `bash` | ✅ | |
| `read_file` | ✅ | |
| `write_file` | ✅ | |
| `edit_file` | ✅ | |
| `read_chunk` | ✅ | |
| `web_search` | ✅ | |
| `web_fetch` | ✅ | |
| `search_knowledge` | ✅ | |
| `calculator` | ✅ | |
| `get_time` | ✅ | |
| `todo_write` | ❌ | 规划归主 Agent |
| `task` | ❌ | 防递归委派 |

**总计**: 10 个工具可用（11 个现有工具 − task − todo_write），2 个排除。

---

## State Transitions

```
┌──────────────┐
│  主 Agent    │
│  (正常循环)   │
└──────┬───────┘
       │ LLM 调用 task(description)
       ▼
┌──────────────┐
│ TaskTool.run │──► spawn_subagent(description, llm, executor)
└──────┬───────┘
       │ SubAgent(llm=llm, executor=executor)
       │   → Agent.__init__(
       │       system_prompt=SUB_SYSTEM_PROMPT,
       │       max_steps=30,
       │       tool_filter={"task", "todo_write"},
       │       print_handler=sub_print_handler
       │     )
       │   → self._round = 0
       ▼
┌──────────────┐
│  SubAgent    │──► 独立循环 (Think → Act → Observe)
│  (执行中)     │    • 新鲜 messages[]
│              │    • 10 个工具 (filtered)
│              │    • [sub] 终端输出
│              │    • 第 30 轮注入提醒
│              │    • _round 自增
└──────┬───────┘
       │ 终止条件满足
       ▼
┌──────────────┐
│  返回结论     │──► text 追加到主 Agent messages
│  (文本)       │    作为 task 工具的工具结果
└──────────────┘
```
