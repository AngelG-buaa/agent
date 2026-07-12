# Quickstart: Context Compact

**Feature**: 003-context-compact | **Date**: 2026-07-12

验证 Context Compact 功能端到端可用的步骤指南。

## 前置条件

- Python 3.12+
- 已安装依赖：`pip install openai python-dotenv`
- `.env` 文件中配置了 `ANTHROPIC_BASE_URL`、`MODEL_ID` 等 LLM 连接参数
- 项目根目录为 `Stage 2/project/`

## 验证场景

### 场景 1：短对话不触发任何压缩（SC-006）

验证压缩管线在短对话中完全透明。

```bash
cd "d:\LLM\Agent\Stage 2\project"
# 运行一个简单问题，只读一个小文件
```

**预期结果**：
- Agent 正常回答，无 `[auto compact]` 输出
- 行为与未添加压缩功能时完全一致

### 场景 2：L3 大结果持久化（SC-001）

验证超大 bash 输出自动降级。

**构造条件**：
- 让 Agent 执行返回 600KB+ 输出的 bash 命令（如 `type` 一个大文件或 `dir /s` 大目录）

**预期结果**：
- 命令输出被写入 `.task_outputs/tool-results/<tool_call_id>.txt`
- 消息中仅保留 `<persisted-output>` 标记 + 文件路径 + 2000 字符预览
- Agent 继续正常工作，不崩溃

### 场景 3：L1 消息截断 + tool 配对保护（SC-002）

验证长对话自动截断。

**构造条件**：
- 模拟或构造 150+ 条消息的对话历史
- 确保中间有 assistant tool_calls + tool 结果配对

**预期结果**：
- 消息缩减至 ≤101 条
- 边界处 tool_use/tool_result 配对完整
- API 调用不因消息格式错误而失败

### 场景 4：L2 旧工具结果占位符化（SC-003）

验证旧 tool_result 自动精简。

**构造条件**：
- 包含 10+ 个 tool_result 的对话，其中 7+ 个 >120 字符

**预期结果**：
- 最新 5 个保持完整
- 其余长结果被替换为 `[Earlier tool result compacted. Re-run if needed.]`
- 短结果（≤120 字符）不受影响

### 场景 5：L4 摘要压缩 + Todo 恢复（SC-004, SC-005）

验证完整的 LLM 摘要流程和状态连续性。

**构造条件**：
- 使用 todo_write 创建 3 个任务（1 completed, 1 in_progress, 1 pending）
- 制造超过 200,000 字符的长对话（多次读大文件、执行命令）

**预期结果**：
- 终端输出 `[auto compact]`
- `.transcripts/transcript_<timestamp>.jsonl` 包含完整对话
- messages 缩减为 1-2 条（摘要 + todo 恢复消息）
- 恢复的 todo 列表正确显示 3 个任务及其状态
- Agent 继续工作，不重复完成任务

### 场景 6：L4 失败降级

验证摘要调用失败时的降级行为。

**构造条件**（需要模拟 API 错误）：
- 临时修改 `LLMClient.chat()` 使其对特定 prompt 抛出异常
- 触发 L4 压缩

**预期结果**：
- 重试 2 次后跳过压缩
- 保留原始消息继续后续 LLM 调用
- Agent 不崩溃

## 单元测试运行

```bash
cd "d:\LLM\Agent\Stage 2\project"
python -m pytest tests/test_compact.py -v
```

**预期结果**：
- L1/L2/L3 各层的独立测试全部通过
- 边界条件覆盖（空消息列表、恰好等于阈值、tool_use 边界重叠）

## 配置调整

所有阈值在 `config.py` 中配置：

```python
# config.py
compaction = CompactionConfig(
    context_limit=200_000,        # L4 触发阈值
    max_messages_snip=100,        # L1 触发阈值
    keep_recent_tool_results=5,   # L2 保留数
    tool_result_budget_bytes=500_000,  # L3 预算
    persist_threshold=30_000,     # L3 单条阈值
    summary_retry_count=2,        # L4 重试次数
)
```

修改后重启 Agent 即可生效。
