# Contracts: 交互式对话

**Feature**: 004-interactive-conversation
**Date**: 2026-07-12

## 1. ask_user Tool Contract

### Schema (OpenAI Function Calling Format)

```json
{
  "type": "function",
  "function": {
    "name": "ask_user",
    "description": "向用户提问以澄清歧义或获取偏好。仅在无法通过其他工具获取信息、且用户输入无法确定意图时使用。同一问题不应重复询问。问题应包含足够的背景信息（当前在做什么、为什么需要用户输入），使用户无需查看对话历史即可理解并回答。",
    "parameters": {
      "type": "object",
      "properties": {
        "question": {
          "type": "string",
          "description": "向用户提出的问题。应包含清晰的背景说明和可选选项（如适用）。例如：'当前有 data.json 和 data.csv 两个文件，你想分析哪一个？'"
        }
      },
      "required": ["question"]
    }
  }
}
```

### Input-Output Contract

| Direction | Format | Description |
|-----------|--------|-------------|
| LLM → Tool | `{"question": "..."}` | Agent 调用 ask_user 时传入 |
| Tool → User | `print("❓ Agent 提问: {question}")` | 终端显示问题 |
| User → Tool | `input("👤 你的回答: ")` | 阻塞等待用户输入 |
| Tool → LLM | `{"answer": "..."} or {"answer": "...", "is_valid": false}` | 回答作为 tool result 注入对话 |

### Error Cases

| Condition | Return |
|-----------|--------|
| `question` 为空字符串 | `{"error": "question 参数不能为空"}` |
| 用户按 Ctrl+C 跳过 | `{"answer": "用户未回答（中断）", "is_valid": false}` |
| 用户输入空 | `{"answer": "用户未提供回答", "is_valid": false}` |
| 用户输入"不知道"/"随便"等 | `{"answer": "<原始输入>", "is_valid": false}` |

---

## 2. REPL Command Contract

### Input Commands

| Command | Behavior | Interception Layer |
|---------|----------|-------------------|
| `/exit` | 退出程序，提示未完成任务（如有） | Conversation（不送入 LLM） |
| `/quit` | 同 `/exit` | Conversation |
| 空输入 | 忽略，重新显示提示符 | Conversation |
| Ctrl+C (首次) | 中断当前 Agent 轮次，回到输入状态 | Conversation → Agent |
| Ctrl+C (二次) | 退出程序 | Conversation |

### Output Format

```
🤖 myAgent 已启动。输入 /exit 退出，Ctrl+C 中断当前操作。

👤 你: <user input>
🤖 MyAgent: <agent response>

👤 你: <next input>
...
```
