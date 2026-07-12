# Data Model: Context Compact

**Feature**: 003-context-compact | **Date**: 2026-07-12

## Entities

### CompactionConfig

压缩管线配置参数。所有阈值可通过 `config.py` 调整。

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| context_limit | int | 200_000 | L4 触发阈值（字符数） |
| max_messages_snip | int | 100 | L1 触发阈值（消息数） |
| head_count | int | 3 | L1 头部保留消息数 |
| keep_recent_tool_results | int | 5 | L2 保留最新 tool_result 数 |
| min_content_length | int | 120 | L2 最小替换内容长度（字符） |
| tool_result_budget_bytes | int | 500_000 | L3 单轮 tool_result 总预算（字节） |
| persist_threshold | int | 30_000 | L3 单条 tool_result 持久化阈值（字节） |
| persist_preview_chars | int | 2_000 | L3 持久化预览长度（字符） |
| summary_max_tokens | int | 2_000 | L4 摘要生成 max_tokens |
| summary_retry_count | int | 2 | L4 摘要失败重试次数 |
| summary_input_cap | int | 80_000 | L4 输入对话截断上限（字符） |

### CompactedMessage (概念实体)

经过压缩管线处理后的消息列表。不是独立的数据结构——直接复用 OpenAI 消息格式的 `list[dict]`。

压缩管线各层对消息的修改：

| Layer | 修改方式 | 受影响的消息 |
|-------|---------|------------|
| L3 budget | 替换 content（完整输出 → `<persisted-output>` 标记） | 最新一轮 `role=tool` 消息 |
| L1 snip | 删除中间消息 + 插入占位符 | 中间段消息（head 之后、tail 之前） |
| L2 micro | 替换 content（完整结果 → 占位符文本） | 旧的 `role=tool` 消息 |
| L4 compact | 全部替换为 1 条摘要消息 + 可选 todo 恢复 | 全部消息（system 消息除外） |

### TranscriptRecord (L4 产出)

L4 触发时保存的完整对话存档。

| Field | Type | Description |
|-------|------|-------------|
| file_path | Path | `.transcripts/transcript_<timestamp>.jsonl` |
| format | str | JSONL — 每行一条 JSON 消息 |
| content | list[dict] | 完整的 messages 列表（压缩前） |
| timestamp | float | `time.time()` 生成的文件名时间戳 |

### PersistedToolResult (L3 产出)

L3 触发时写入磁盘的大工具输出。

| Field | Type | Description |
|-------|------|-------------|
| file_path | Path | `.task_outputs/tool-results/<tool_call_id>.txt` |
| tool_call_id | str | OpenAI 工具调用 ID（文件名来源） |
| original_content | str | 完整原始输出 |
| preview | str | 前 2000 字符预览（保留在消息中） |

### TodoState (Post-Compact 恢复来源)

L4 压缩后恢复的来源数据。直接从 `tools.todo_write.CURRENT_TODOS` 全局变量读取。

| Field | Type | Description |
|-------|------|-------------|
| todos | list[dict] | 任务列表，每项含 `content` (str) + `status` (str) |

状态枚举：`pending` | `in_progress` | `completed`

## State Transitions

### 压缩管线状态机

```
IDLE (messages 正常)
  │
  ├── L3 触发 (最新 tool_result 超预算)
  │   └── PERSISTED (部分 tool_result 写入磁盘)
  │
  ├── L1 触发 (消息数 > 100)
  │   └── SNIPPED (中间消息被裁剪)
  │
  ├── L2 触发 (tool_result 数 > 5)
  │   └── MICRO_COMPACTED (旧 tool_result 被替换)
  │
  └── L4 触发 (大小 > 200K chars)
      ├── 成功 → COMPACTED (全量替换为摘要 + todo 恢复)
      └── 失败 → DEGRADED (重试耗尽，跳过压缩，保留原始消息)
```

### L4 重试状态

```
L4_TRIGGERED
  ├── attempt_1: success → COMPACTED
  ├── attempt_1: failure → attempt_2
  │   ├── success → COMPACTED
  │   └── failure → DEGRADED (skip compaction)
  └── ...
```

## Relationships

```
Agent.run()
  └── messages: list[dict]  ←── compact_pipeline() 修改
        │
        ├── L3 产出 PersistedToolResult (写入磁盘，消息中留引用)
        ├── L1 产出 snipped 占位符消息
        ├── L2 产出 compacted 占位符消息
        └── L4 产出 TranscriptRecord (存档) + 摘要消息 + TodoState 注入
```
