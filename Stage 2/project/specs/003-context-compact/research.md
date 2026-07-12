# Research: Context Compact

**Feature**: 003-context-compact | **Date**: 2026-07-12

## 1. 消息格式适配

### Decision
所有压缩层基于 OpenAI 消息格式（role: system/user/assistant/tool，tool_call_id 关联），不照搬 Anthropic content-block 格式。

### Rationale
项目 LLM 客户端 (`agent/llm_client.py`) 使用 OpenAI 兼容 API。消息结构为：
- `{"role": "system", "content": "..."}`
- `{"role": "user", "content": "..."}`
- `{"role": "assistant", "content": "...", "tool_calls": [...]}`
- `{"role": "tool", "tool_call_id": "...", "content": "..."}`

这与教学版 (s08_context_compact) 的 Anthropic block-list 格式完全不同，所有 helper 函数必须重写。

### Key Format Differences

| 检测 | Anthropic (教学版) | OpenAI (本项目) |
|------|-------------------|----------------|
| tool_use 消息 | `msg["content"][i]["type"] == "tool_use"` | `msg["role"] == "assistant" and msg.get("tool_calls")` |
| tool_result 消息 | `msg["content"][i]["type"] == "tool_result"` | `msg["role"] == "tool"` |
| tool_use ID | `block["id"]` | `tc["id"]` (在 tool_calls 数组中), `msg["tool_call_id"]` (在 tool 消息中) |
| 配对关联 | 同一轮内的 block 顺序 | `tool_call_id` 跨消息关联 |

### Alternatives Considered
- 改用 Anthropic 格式：需要重写整个 LLM 客户端和工具执行器，成本过高
- 格式抽象层：过度设计，当前只需适配一种格式

---

## 2. 压缩管线注入点

### Decision
在 `Agent.run()` 循环体内、`llm.chat()` 调用之前，注入一行 `compact_pipeline(messages)` 调用。不使用 PreLLMCall hook。

### Rationale
Hook 系统 (`hooks.py`) 的回调通过 `trigger_hooks` 传递 `*args`，回调签名固定，不方便接收和修改 `messages` 列表（PreLLMCall 只支持注入新消息，不支持修改已有消息）。压缩管线需要直接修改 messages（替换、删除、截断），在循环体内直接调用更清晰。

符合 Constitution 原则 IX：循环体仅新增 1 行调用，不塞入业务逻辑，不影响 Think→Act→Observe 结构。

### Alternatives Considered
- PreLLMCall hook：当前 hook 只能注入新消息（`return {"messages": [...]}`），不能裁剪已有消息。需要扩展 hook 协议才能支持，属于过度设计。
- PostRound hook：执行时机在工具调用之后，上下文可能已经爆炸（本轮超大工具结果已加入 messages），不如 Pre-LLM 时机安全。

### Injection Point (pseudocode)

```python
# agent/agent.py Agent.run()
for _ in range(self.max_steps):
    # ... PreLLMCall hook (existing) ...

    compact_pipeline(messages)          # <-- NEW: single line

    stop_reason, msg = self.llm.chat(messages, schemas)
    # ... rest of loop unchanged ...
```

---

## 3. Token/大小估算

### Decision
使用 `len(str(messages))` 字符数近似估算，不引入 tiktoken 精确计数。

### Rationale
- DeepSeek-V4-Pro 上下文窗口为 1M tokens，阈值 200K 字符非常保守（约 100K tokens），有充足安全余量
- tiktoken 的 cl100k_base 编码器与 DeepSeek tokenizer 不完全一致，精确计数意义有限
- 字符数估算简单、零依赖、零开销
- 参考 Claude Code 生产版也使用简化的 token 估算（非精确计数）

### Alternatives Considered
- tiktoken 精确计数：增加依赖，但不同模型 tokenizer 不通用，实际精度提升有限
- API 返回的 usage 信息：只能事后获取，无法用于事前判断

---

## 4. L4 摘要 Prompt 设计

### Decision
复用教学版的核心提示词结构，用中文编写（与系统 prompt 语言一致）。关键的 5 个保留维度：当前目标、关键发现/决策、已读/已改文件、剩余工作、用户约束。

### Rationale
教学版的摘要 prompt 经过验证有效。翻译为中文以匹配项目的 `SYSTEM_PROMPT` 语言（中文），确保摘要与对话上下文语言一致。

### Prompt 结构

```
请总结以下编程助手的对话历史，使工作可以继续。
必须保留：
1. 当前目标和任务
2. 关键发现和决策
3. 已读取和已修改的文件
4. 剩余工作
5. 用户约束和偏好
请简洁但具体。

[对话全文，截断至 80,000 字符]
```

### Alternatives Considered
- 英文 prompt：摘要质量可能更好（训练数据更多），但与中文对话上下文不一致
- 分块总结（map-reduce）：更精确但需要多次 API 调用，成本过高

---

## 5. L3 持久化路径设计

### Decision
持久化到 `.task_outputs/tool-results/<tool_call_id>.txt`，消息中保留 2000 字符预览。

### Rationale
- 目录与 Claude Code 的 `.task_outputs/` 命名一致，便于理解
- 使用 `tool_call_id` 作为文件名，保证唯一性（OpenAI API 生成的 ID 是唯一的）
- 2000 字符预览让 LLM 能够判断是否需要读取完整文件，避免盲目重读

### Alternatives Considered
- 使用 hash 命名：增加复杂度，tool_call_id 已足够唯一
- 使用 `.json` 格式：工具结果已是 JSON 字符串，`.txt` 更通用

---

## 6. Todo 恢复格式

### Decision
L4 压缩后，若 `CURRENT_TODOS` 非空，追加一条 user 消息，将 todo 列表格式化为 Markdown 任务列表。

### Rationale
`CURRENT_TODOS` 是全局变量（`tools/todo_write.py`），可直接 import。恢复消息使用 `role="user"`（与摘要消息一致），确保 LLM 将其视为对话上下文的一部分。

### 恢复消息格式

```
## 当前任务进度（压缩后恢复）

- [✓] 已完成的任务
- [▸] 正在进行中的任务
- [ ] 待完成的任务
```

### Alternatives Considered
- role="system"：system 消息始终是第一条，插入中间会破坏消息结构
- 不恢复：压缩后 Agent 丢失进度，可能重复工作或遗漏步骤
