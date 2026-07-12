# Implementation Plan: Context Compact（上下文压缩）

**Branch**: `003-context-compact` | **Date**: 2026-07-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-context-compact/spec.md`

## Summary

为 Agent 添加四层渐进式上下文压缩管线，在每轮 LLM 调用前自动运行。零成本操作优先（L1-L3），仅在必要时触发 LLM 摘要（L4）。压缩后自动恢复 Todo 列表状态。

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: openai (已有), 无新增依赖
**Storage**: `.transcripts/`（JSONL）、`.task_outputs/tool-results/`（文本文件）
**Testing**: pytest
**Target Platform**: Windows/Linux CLI
**Project Type**: CLI agent 应用

## Constitution Check

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Correctness First | ✅ | 每层先跑通正确路径，再处理边界 |
| II | Small Steps | ✅ | 模块 `compact.py`，每层独立可测 |
| III | Clarity & Maintainability | ✅ | 四个纯函数，单职责 |
| IV | Good Architecture | ✅ | agent 层内部新增，config 统一管理阈值 |
| V | Don't Reinvent | ✅ | 四层管线设计来自 Claude Code 验证方案 |
| VI | Mainstream Practices | ✅ | 渐进式压缩行业标准 |
| VII | Unit Tests | ✅ | 每层独立测试覆盖 |
| VIII | Backward Compatibility | ✅ | `Agent.run()` 接口不变 |
| IX | Keep Agent Loop Simple | ✅ | 循环体内仅一行 `compact_pipeline(messages, self.llm)` |

## Detailed Design

### 0. 为什么不用 Hook？

| 需求 | Hook 能力 | 差距 |
|------|----------|------|
| 修改已有消息 content | ❌ PreLLMCall 只能返回 `{"messages": [...]}` 追加 | 无法原地修改 |
| 删除中间消息 | ❌ 同上 | 无法删除 |
| 在特定位置插入消息 | ❌ 同上（只能追加到末尾） | 无法在中间插入 snipped 标记 |
| 全量替换消息列表 | ❌ 同上 | 无法删除已有消息后追加 |

要让 hook 支持 compact，必须修改 hook 协议（让 PreLLMCall 接收 `messages` 引用），这是 **breaking change**，影响已有回调。结论：**inline 一行委托调用**，不修改 hook 协议。

---

### 1. Agent.run() 改动

**改动点：** 在 `llm.chat()` 之前插入一行。位置选在 PreLLMCall 之后、get_schemas 之前。

```python
# agent/agent.py Agent.run() 循环体内

for _ in range(self.max_steps):
    # ---- 已有：Hook 注入（todo 提醒等）----
    inject = trigger_hooks("PreLLMCall")
    if inject:
        messages.extend(inject["messages"])

    # ---- 新增：压缩管线（仅此一行）----
    from agent.compact import compact_pipeline
    compact_pipeline(messages, self.llm)

    # ---- 已有：获取 schema，调用 LLM ----
    schemas = self.executor.get_schemas()
    if self.tool_filter:
        schemas = [s for s in schemas if s["function"]["name"] not in self.tool_filter]
    stop_reason, msg = self.llm.chat(messages, schemas)

    # ... 后续不变 ...
```

**为什么放在 PreLLMCall 之后？** 因为 todo 提醒 hook 可能刚追加了新消息，compact 应该基于最终的消息列表来判断是否需要压缩。

**为什么放在 get_schemas 之前？** 无依赖关系，放在 llm.chat 之前的任意位置均可。选在 hook 之后即可。

---

### 2. compact_pipeline() 总调度

```python
# agent/compact.py

def compact_pipeline(messages: list[dict], llm: LLMClient) -> None:
    """四层压缩管线，原地修改 messages。执行顺序 L3→L1→L2→L4。"""
    
    # L3: 大结果持久化 —— 在 L2 之前执行，因为 L2 会替换掉大内容
    tool_result_budget(messages)
    
    # L1: 消息截断 —— 在 L2 之前执行，先把明显多余的消息裁掉
    snip_compact(messages)
    
    # L2: 旧结果占位符 —— 在 L4 之前执行，尽量让 L4 不必触发
    micro_compact(messages)
    
    # L4: LLM 摘要 —— 前三层都挡不住时，发起 API 调用
    if _estimate_size(messages) > CONTEXT_LIMIT:
        print("[auto compact]")
        compact_history(messages, llm)
```

**关键约束**：L3 必须在 L2 之前。理由——L2 把旧 tool_result 替换为占位符后，L3 看到的都是短字符串，无法判断哪些结果需要持久化。必须先用完整内容做预算判断。

---

### 3. L3: tool_result_budget() — 大结果持久化

**算法**：

```python
def tool_result_budget(messages: list[dict]) -> None:
    """
    找到最近一轮 assistant tool_calls 对应的所有 tool 消息。
    如果总大小 > TOOL_RESULT_BUDGET_BYTES (500KB)，
    且存在单个 > PERSIST_THRESHOLD (30KB) 的结果，
    则按从大到小顺序持久化到磁盘，直到总量降到阈值以下。
    """
    # 1. 找到最近一条 assistant 消息（含 tool_calls）
    last_assistant_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "assistant" and messages[i].get("tool_calls"):
            last_assistant_idx = i
            break
    if last_assistant_idx is None:
        return  # 还没有 tool_calls，无需处理
    
    # 2. 收集这轮 tool_calls 的所有 tool_call_id
    tc_ids = {tc["id"] for tc in messages[last_assistant_idx]["tool_calls"]}
    
    # 3. 找到这些 tool_call_id 对应的 tool 消息（在 assistant 消息之后）
    tool_msgs = []
    for i in range(last_assistant_idx + 1, len(messages)):
        msg = messages[i]
        if msg.get("role") == "tool" and msg.get("tool_call_id") in tc_ids:
            tool_msgs.append((i, msg))
    
    if not tool_msgs:
        return  # 这轮没有 tool 结果
    
    # 4. 计算总大小
    total = sum(len(m[1].get("content", "")) for m in tool_msgs)
    if total <= TOOL_RESULT_BUDGET_BYTES:
        return  # 未超预算
    
    # 5. 按内容从大到小排序
    tool_msgs.sort(key=lambda m: len(m[1].get("content", "")), reverse=True)
    
    # 6. 依次持久化，直到总量降到阈值以下
    _ensure_dir(TOOL_RESULTS_DIR)
    for idx, msg in tool_msgs:
        content = msg.get("content", "")
        if len(content) <= PERSIST_THRESHOLD:
            continue  # 小结果不持久化
        
        tool_call_id = msg.get("tool_call_id", "unknown")
        filepath = TOOL_RESULTS_DIR / f"{tool_call_id}.txt"
        filepath.write_text(content, encoding="utf-8")
        
        # 替换消息内容为预览 + 指针
        preview = content[:PERSIST_PREVIEW_CHARS]
        msg["content"] = (
            f"<persisted-output>\n"
            f"Full output: {filepath}\n"
            f"Preview:\n{preview}\n"
            f"</persisted-output>"
        )
        
        # 重新计算总量
        total = sum(len(m[1].get("content", "")) for m in tool_msgs)
        if total <= TOOL_RESULT_BUDGET_BYTES:
            break
```

**设计细节**：
- 通过追溯最近一条 `assistant` + `tool_calls` 消息来定位"当前轮"，然后收集其 `tool_call_id` 集合对应的所有 `tool` 消息。这正确处理了多工具并行调用场景（如 `[bash(100KB), read_file(1KB), glob(1KB)]`）。
- 排序：按 content 从大到小处理，用最少的磁盘写入解决问题。
- 历史消息中的大 tool 结果由 L2 处理，L3 只关注当前轮。
- 文件名用 `tool_call_id`：OpenAI 保证唯一性。

---

### 4. L1: snip_compact() — 消息截断

**算法**：

```python
def snip_compact(messages: list[dict], max_messages: int = 100) -> None:
    """
    当消息总数 > max_messages 时，保留前 HEAD_COUNT 条和后 (max_messages - HEAD_COUNT) 条，
    中间替换为一条 snipped 占位符。
    
    边界保护：确保 tool_use/tool_result 配对不被撕开。
    """
    if len(messages) <= max_messages:
        return
    
    HEAD_COUNT = 3
    keep_head = HEAD_COUNT
    keep_tail = max_messages - HEAD_COUNT  # 97
    
    head_end = keep_head           # head 区间的右边界（不含）
    tail_start = len(messages) - keep_tail  # tail 区间的左边界（含）
    
    # ---- 边界保护 1：head 最后一条是 assistant tool_calls ----
    # 对应的 tool 结果消息必须一起保留
    if head_end > 0 and _has_tool_calls(messages[head_end - 1]):
        # 往后扫描，把属于这批 tool_calls 的 tool 结果也拉进来
        tc_ids = {tc["id"] for tc in messages[head_end - 1].get("tool_calls", [])}
        while head_end < len(messages) and _is_tool_for_ids(messages[head_end], tc_ids):
            head_end += 1
    
    # ---- 边界保护 2：tail 第一条是孤立的 tool 结果 ----
    # 对应的 assistant tool_calls 在它前面，必须一起保留
    if tail_start > 0 and messages[tail_start].get("role") == "tool":
        # 往前找配对的 assistant 消息
        tc_id = messages[tail_start].get("tool_call_id")
        if tc_id and tail_start > 0 and _has_tool_call_id(messages[tail_start - 1], tc_id):
            tail_start -= 1
    
    # ---- 检查是否重叠 ----
    if head_end >= tail_start:
        return  # 保护逻辑导致 head 和 tail 连成一片，没什么可裁
    
    # ---- 执行截断 ----
    snipped_count = tail_start - head_end
    snipped_msg = {
        "role": "user",
        "content": f"[snipped {snipped_count} messages]"
    }
    messages[:] = messages[:head_end] + [snipped_msg] + messages[tail_start:]
```

**OpenAI 格式下的 helper 函数**：

```python
def _has_tool_calls(msg: dict) -> bool:
    """消息是 assistant 且包含 tool_calls。"""
    return msg.get("role") == "assistant" and bool(msg.get("tool_calls"))

def _is_tool_for_ids(msg: dict, tc_ids: set) -> bool:
    """消息是 tool 结果，且 tool_call_id 属于给定集合。"""
    return msg.get("role") == "tool" and msg.get("tool_call_id") in tc_ids

def _has_tool_call_id(msg: dict, tc_id: str) -> bool:
    """消息是 assistant 且 tool_calls 中包含给定的 id。"""
    if msg.get("role") != "assistant":
        return False
    return any(tc["id"] == tc_id for tc in msg.get("tool_calls", []))
```

**边界保护示意**：

```
截断前 (head_end=3, tail_start=50):
 [sys] [user] [assistant(tool_calls:{id=abc})]     ← head 第 3 条
        [tool(tool_call_id=abc)]                    ← 被边界保护拉入 head
        [tool(tool_call_id=abc)]                    ← 被边界保护拉入 head
        ... [47 条中间消息] ...
        [assistant(tool_calls:{id=xyz})]            ← 被边界保护拉入 tail
        [tool(tool_call_id=xyz)]                    ← tail 第 1 条，触发保护
        [assistant] [tool] ... [user]               ← tail

截断后:
 [sys] [user] [assistant(tc:abc)] [tool(abc)] [tool(abc)]
 [user: "[snipped 47 messages]"]
 [assistant(tc:xyz)] [tool(xyz)] [assistant] [tool] ... [user]
```

---

### 5. L2: micro_compact() — 旧工具结果占位符化

**算法**：

```python
KEEP_RECENT = 5
MIN_CONTENT_LENGTH = 120
PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"

def micro_compact(messages: list[dict]) -> None:
    """
    遍历所有 role="tool" 的消息，保留最新 KEEP_RECENT 个的完整内容，
    其余 content 长度 > MIN_CONTENT_LENGTH 的替换为占位符。
    """
    # 1. 收集所有 tool 消息的索引
    tool_indices = [
        i for i, msg in enumerate(messages)
        if msg.get("role") == "tool"
    ]
    
    if len(tool_indices) <= KEEP_RECENT:
        return
    
    # 2. 旧的（不在最新 KEEP_RECENT 个中的）替换为占位符
    for idx in tool_indices[:-KEEP_RECENT]:
        content = messages[idx].get("content", "")
        if len(content) > MIN_CONTENT_LENGTH:
            messages[idx]["content"] = PLACEHOLDER
```

**为什么 L2 在 L1 之后执行？**
- L1 先裁掉中间整段消息（含其中的 tool_result），减少 L2 需要遍历的消息数
- 如果 L2 先执行，替换了一堆占位符后，L1 再把它们裁掉——白费力气

---

### 6. L4: compact_history() — LLM 摘要

**算法**：

```python
def compact_history(messages: list[dict], llm: LLMClient) -> None:
    """
    保存完整对话 → 调 LLM 生成摘要 → 用摘要替代全部历史。
    重试机制：最多重试 SUMMARY_RETRY_COUNT 次，全部失败则降级跳过。
    """
    # 1. 保存完整对话到磁盘
    _ensure_dir(TRANSCRIPT_DIR)
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with transcript_path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
    
    # 2. 构建摘要 prompt
    conversation_text = json.dumps(messages, ensure_ascii=False, default=str)
    if len(conversation_text) > SUMMARY_INPUT_CAP:
        conversation_text = conversation_text[:SUMMARY_INPUT_CAP]
    
    summary_prompt = (
        "请总结以下编程助手的对话历史，使工作可以继续。\n"
        "必须保留：\n"
        "1. 当前目标和任务\n"
        "2. 关键发现和决策\n"
        "3. 已读取和已修改的文件\n"
        "4. 剩余工作\n"
        "5. 用户约束和偏好\n"
        "请简洁但具体。\n\n"
        + conversation_text
    )
    
    # 3. 带重试的 LLM 摘要调用
    summary = None
    for attempt in range(SUMMARY_RETRY_COUNT + 1):
        try:
            # 使用简单的 chat 调用（不带工具，限制输出长度）
            _, response = llm.chat(
                messages=[{"role": "user", "content": summary_prompt}],
                tools=[],           # 摘要不需要工具
                max_tokens=SUMMARY_MAX_TOKENS  # 限制摘要长度
            )
            summary = response.content if hasattr(response, 'content') else str(response)
            if summary:
                break
        except Exception:
            if attempt == SUMMARY_RETRY_COUNT:
                # 全部重试耗尽，降级：跳过压缩
                return
            continue
    
    if not summary:
        return  # 所有尝试都失败，降级
    
    # 4. 用摘要消息替代全部历史
    compact_msg = {
        "role": "user",
        "content": f"[Compacted]\n\n{summary}"
    }
    messages[:] = [compact_msg]
    
    # 5. Post-compact: 恢复 Todo 列表
    _restore_todos(messages)


def _restore_todos(messages: list[dict]) -> None:
    """L4 压缩后恢复 Todo 列表到 messages。"""
    try:
        from tools.todo_write import CURRENT_TODOS
    except ImportError:
        return
    
    if not CURRENT_TODOS:
        return
    
    STATUS_ICONS = {"pending": " ", "in_progress": "▸", "completed": "✓"}
    lines = ["## 当前任务进度（压缩后恢复）", ""]
    for t in CURRENT_TODOS:
        icon = STATUS_ICONS.get(t.get("status", "pending"), " ")
        lines.append(f"- [{icon}] {t.get('content', '')}")
    
    messages.append({
        "role": "user",
        "content": "\n".join(lines)
    })
```

**L4 为什么把 system prompt 也替换掉了？**

L4 替换的是 `messages[:]`（全部消息），包括最初的 system 消息。但这不会丢失 system prompt，因为 `llm.chat()` 每次调用时 `tools` 参数独立发送 tool schema，而 system prompt... 等等，当前代码确实是 `messages.append({"role": "system", ...})`，system prompt 在 messages 里。

所以这里需要特殊处理：**保留第一条 system 消息**。

```python
# 修正：保留 system 消息
system_msg = messages[0] if messages and messages[0]["role"] == "system" else None
messages[:] = [compact_msg]
if system_msg:
    messages.insert(0, system_msg)
```

---

### 7. 辅助函数

```python
import os
from pathlib import Path

# 目录常量
WORKDIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"

# 阈值（从 config 导入，这里列出默认值）
CONTEXT_LIMIT = 200_000          # L4 触发阈值（字符）
MAX_MESSAGES_SNIP = 100          # L1 触发阈值（消息数）
KEEP_RECENT = 5                  # L2 保留数
MIN_CONTENT_LENGTH = 120         # L2 最小替换长度
TOOL_RESULT_BUDGET_BYTES = 500_000  # L3 预算
PERSIST_THRESHOLD = 30_000       # L3 单条持久化阈值
PERSIST_PREVIEW_CHARS = 2_000    # L3 预览长度
SUMMARY_MAX_TOKENS = 2_000       # L4 摘要 max_tokens
SUMMARY_RETRY_COUNT = 2          # L4 重试次数
SUMMARY_INPUT_CAP = 80_000       # L4 输入截断


def _estimate_size(messages: list[dict]) -> int:
    """估算消息列表的字符数。"""
    return len(str(messages))


def _ensure_dir(path: Path) -> None:
    """确保目录存在，不存在则创建。"""
    path.mkdir(parents=True, exist_ok=True)
```

---

### 8. config.py 扩展

```python
@dataclass(frozen=True)
class CompactionConfig:
    context_limit: int = 200_000
    max_messages_snip: int = 100
    head_count: int = 3
    keep_recent_tool_results: int = 5
    min_content_length: int = 120
    tool_result_budget_bytes: int = 500_000
    persist_threshold: int = 30_000
    persist_preview_chars: int = 2_000
    summary_max_tokens: int = 2_000
    summary_retry_count: int = 2
    summary_input_cap: int = 80_000

compaction = CompactionConfig()
```

---

### 9. LLMClient 兼容性

L4 调用 `llm.chat()` 时需要传空的 tools 列表。当前 `LLMClient.chat()` 签名为：

```python
def chat(self, messages: list, tools: list) -> tuple[str, object]:
```

`tools=[]` 也是合法的——让 LLM 做纯文本回复（生成摘要）。**不需要修改 LLMClient**。

但需注意当前实现没有 `max_tokens` 参数。对于摘要生成，需要限制输出长度。如果 LLM 客户端不支持 `max_tokens`，则依赖 prompt 中的"请简洁"和摘要 prompt 的输入截断来控制。

`LLMClient.chat()` 已支持可选的 `max_tokens` 参数（默认 None，向后兼容）。L4 摘要调用时传入 `max_tokens=SUMMARY_MAX_TOKENS` 以限制摘要输出长度。

---

### 10. 完整调用链路

```
agent.run()
  └─ for each step:
       ├─ PreLLMCall hooks (已有: todo 提醒注入)
       ├─ compact_pipeline(messages, llm)          ← 新增：1 行
       │    ├─ tool_result_budget(messages)         ← L3: 0 API 调用
       │    │    └─ 找到最新 tool 消息
       │    │        ├─ size ≤ 500KB → 跳过
       │    │        └─ size > 500KB → 写入磁盘 + 替换为预览
       │    ├─ snip_compact(messages)               ← L1: 0 API 调用
       │    │    ├─ len ≤ 100 → 跳过
       │    │    └─ len > 100 → head/tail 保留 + 边界保护 + 中间截断
       │    ├─ micro_compact(messages)              ← L2: 0 API 调用
       │    │    ├─ tool_result 数 ≤ 5 → 跳过
       │    │    └─ tool_result 数 > 5 → 旧结果替换为占位符
       │    └─ estimate_size > CONTEXT_LIMIT?       ← L4 判断
       │         ├─ No → 跳过
       │         └─ Yes → compact_history(messages, llm)  ← 1 API 调用
       │              ├─ 保存 transcript JSONL
       │              ├─ 调 LLM 生成摘要（最多重试 2 次）
       │              │    ├─ 成功 → 替换 messages 为摘要 + 保留 system
       │              │    └─ 全部失败 → 降级跳过
       │              └─ 恢复 CURRENT_TODOS（若非空）
       ├─ llm.chat(messages, schemas)               ← 已有
       ├─ _execute_tool_calls(...)                  ← 已有
       └─ PostRound hooks                           ← 已有
```

## Project Structure

### Source Code

```text
agent/
├── compact.py           # NEW: ~200 行，四个紧凑函数 + helpers
├── agent.py             # MODIFY: run() 循环体内 +1 行
├── llm_client.py        # NO CHANGE (或可选添加 max_tokens 参数)
├── prompts.py           # NO CHANGE
└── utils.py             # NO CHANGE

config.py                # MODIFY: +CompactionConfig (~15 行)

tests/
├── test_compact.py      # NEW: ~15 个测试用例
```

## Complexity Tracking

> 无 Constitution Check 违规项。
