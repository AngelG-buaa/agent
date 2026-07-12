# Data Model: 交互式对话

**Feature**: 004-interactive-conversation
**Date**: 2026-07-12

## Entities

### Conversation

多轮对话的会话管理器。位于 Agent 之上，负责外循环编排。

| Field | Type | Description |
|-------|------|-------------|
| `agent` | `Agent` | 被编排的 Agent 实例 |
| `messages` | `list[dict]` | 完整消息历史（跨轮累积），格式与 OpenAI messages 兼容 |
| `_first_turn` | `bool` | 是否首轮（控制 system prompt 插入时机） |
| `_interrupted_once` | `bool` | 是否已被第一次 Ctrl+C 中断（用于连续中断保护） |
| `prompt` | `str` | 输入提示符，默认 `"👤 你: "` |

**State Transitions**:

```
[启动] → _first_turn=True, messages=[]
   │
   ├─ 首轮: 插入 system prompt → _first_turn=False
   ├─ 每轮: messages += [user_msg] → Agent.continue_from(messages)
   │          → messages 被 Agent 内部追加 assistant + tool messages
   │          → 回到等待输入
   └─ /exit 或 二次 Ctrl+C → [退出]
```

**Lifecycle**: Conversation 实例生命周期 = 一次程序运行。无持久化，退出即销毁。

---

### Agent Turn

一轮 Agent 执行（一次 `continue_from()` 调用）。非独立实体，是 Conversation 内的逻辑单位。

| Field | Type | Description |
|-------|------|-------------|
| `step_count` | `int` | 本轮已用步数（每次 `continue_from` 重置为 0） |
| `triggered_ask_user` | `bool` | 本轮是否调用了 ask_user |
| `triggered_compact` | `bool` | 本轮是否触发了 compact |
| `stop_reason` | `str` | 终止原因：`"stop"`（LLM 完成）/ `"max_steps"`（超限）/ `"interrupted"`（用户中断） |

---

### AskUserRequest

Agent 向用户发起的单次提问。作为 tool call 内嵌在 messages 流中。

| Field | Type | Description |
|-------|------|-------------|
| `question` | `str` | Agent 提出的问题（由 LLM 生成，含上下文） |
| `answer` | `str` | 用户回答。空字符串表示用户跳过/无法回答 |
| `timestamp` | `str` | 提问时间 ISO 格式（可选，调试用） |

**Relationship**: AskUserRequest 的生命周期嵌在一个 tool_call → tool_result 往返中：
```
assistant: tool_calls=[{function: {name: "ask_user", arguments: {question: "..."}}}]
tool:       {tool_call_id: "...", content: {answer: "user response"}}
```

---

### Messages (OpenAI Format)

跨轮保留的消息列表。因 LLM API 协议决定，非项目自定义。

| Role | Description |
|------|-------------|
| `system` | 仅首轮插入一次（SYSTEM_PROMPT） |
| `user` | 用户输入（每轮一条） |
| `assistant` | Agent 回复（含 tool_calls） |
| `tool` | 工具执行结果（含 ask_user 回答） |

**Key Constraint**: assistant 消息的 `tool_calls[]` 必须与后续 `tool` 消息的 `tool_call_id` 一一对应，否则 API 报错。compact 管线已处理此约束（L1 snipper 的边界保护）。

---

## No New Persistence (v1)

v1 不实现会话持久化。所有数据仅在内存中，退出即销毁：
- `Conversation.messages` — 不落盘
- `PermissionEngine._session_rules` — 不落盘（已存在，不变）
- `CURRENT_TODOS` — 不落盘（已存在，不变）
