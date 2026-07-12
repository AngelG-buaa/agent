# Feature Specification: Context Compact（上下文压缩）

**Feature Branch**: `003-context-compact`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "为 Stage 2/project 添加 Context Compact 功能。实现四层渐进式压缩管线：L3 tool_result_budget（大结果持久化到磁盘）、L1 snip_compact（消息截断）、L2 micro_compact（旧工具结果占位符化）、L4 compact_history（LLM 摘要压缩）。执行顺序 L3→L1→L2→L4，仅自动触发，压缩后恢复 Todo 列表状态。"

## Clarifications

### Session 2026-07-12

- Q: L4 摘要生成 API 调用失败时如何处理？ → A: 重试最多 N 次（N 可配置，默认 2），全部失败后跳过压缩，保留原始消息继续后续 LLM 调用（降级运行）。
- Q: 压缩事件是否应在终端输出提示？ → A: 仅 L4 触发时输出一行提示（如 `[auto compact]`），L1/L2/L3 静默运行。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 超大工具输出自动降级，不撑爆上下文 (Priority: P1)

Agent 在执行 shell 命令或读取大型文件时，单次工具调用可能返回数十万字节的输出。如果没有预算控制，这一轮结果就能耗尽全部上下文窗口，导致后续对话无法继续。系统应在工具结果返回后，自动检测并持久化超大内容到磁盘，在对话中仅保留预览和文件路径。

**Why this priority**: 这是防御上下文爆炸的第一道防线。一个 `cat` 命令或 `grep` 大目录就可能让 Agent 直接崩溃。这是最基础、最紧急的保护。

**Independent Test**: 让 Agent 执行返回 600KB+ 输出的命令，验证系统自动将超过 30KB 的单个结果持久化到 `.task_outputs/tool-results/` 目录，消息中只保留 2000 字符预览和文件路径。

**Acceptance Scenarios**:

1. **Given** Agent 刚执行完一个返回 80KB stdout 的 bash 命令，**When** 该轮所有 tool 消息的总大小超过 500KB 阈值且单个结果超过 30KB，**Then** 该结果被写入 `.task_outputs/tool-results/<tool_call_id>.txt`，消息中保留 `<persisted-output>` 标记、文件路径和 2000 字符预览。
2. **Given** Agent 刚执行了 3 个工具调用，分别返回 200KB、10KB、300KB 内容，总计 510KB 超过阈值，**When** L3 预算检查运行时，**Then** 按内容从大到小依次持久化，直到总量降到 500KB 以下（300KB 那条先处理）。
3. **Given** 单个 tool 消息只有 5KB（≤30KB），**When** L3 检查该结果，**Then** 不会触发持久化（小内容不值得写磁盘）。
4. **Given** 本轮 tool 消息总量不到 500KB，**When** L3 运行时，**Then** 什么都不做，消息原样保留。

---

### User Story 2 - 长对话自动截断中间消息，保护 tool 调用配对 (Priority: P1)

随着对话进行，消息数可能超过 100 条。大部分中间消息（尤其是早期探索性操作）已经不再需要完整保留。系统应自动裁剪中间部分，只保留开头和结尾，同时确保不会把 assistant 的 tool_calls 和对应的 tool 结果消息撕开（破坏消息完整性会导致 API 报错）。

**Why this priority**: 消息截断是最简单的上下文释放手段（0 API 调用），且与 L3 互补——L3 解决单轮超大输出问题，L1 解决历史消息累积问题。两者覆盖不同的上下文消耗模式。

**Independent Test**: 构造一个 120 条消息的对话历史，调用 snip_compact，验证结果 ≤101 条消息（3 head + 1 snipped marker + 97 tail），且 head/tail 边界处没有孤立的 tool_calls 或 tool 结果。

**Acceptance Scenarios**:

1. **Given** 对话有 150 条消息（超过 100 条阈值），**When** L1 运行时，**Then** 保留前 3 条和后 97 条消息，中间 50 条被替换为 `[snipped 50 messages]` 占位符。
2. **Given** 150 条消息中，第 23 条是 assistant（带 tool_calls），第 24-26 条是对应的 tool 结果消息，**When** 正常切割点 head_end=23（第 24 条是 head 最后一条），**Then** 边界保护逻辑触发——将第 24-26 条 tool 结果也拉入 head，head_end 扩展为 26。
3. **Given** 150 条消息中，tail 的第一条（按索引）恰巧是孤立的 tool 结果（对应的 assistant tool_calls 在它前面一条），**When** L1 检测到 tail 边界破坏了配对，**Then** tail_start 前移一条，把配对的 assistant 消息一起保留。
4. **Given** 对话只有 30 条消息（未超过阈值），**When** L1 运行时，**Then** 什么都不做，消息原样返回。

---

### User Story 3 - 旧工具结果自动精简为占位符 (Priority: P2)

Agent 在探索阶段会频繁读取文件和执行命令。早期读取的文件内容在后续阶段通常不再需要（或者可以重新读取）。保留所有历史 tool 消息的完整内容浪费大量上下文。系统应自动将旧的 tool 消息替换为简短的占位符，只保留最近几个的完整内容。

**Why this priority**: L2 在 L1（裁剪消息数）之后、L4（LLM 摘要）之前，进一步释放 token 空间。它比 L4 便宜（0 API 调用），可能让 L4 根本不需要触发。

**Independent Test**: 构造一个包含 10 个 tool 消息的对话，其中 3 个是短结果（≤120 字符），7 个是长结果。调用 micro_compact，验证只有最新 5 个保留完整内容，其余长结果被替换为占位符，短结果不受影响。

**Acceptance Scenarios**:

1. **Given** 对话中有 8 个 tool 消息，其中前 3 个每个超过 120 字符，**When** L2 运行时（KEEP_RECENT=5），**Then** 前 3 个长结果被替换为 `[Earlier tool result compacted. Re-run if needed.]`，最新 5 个保持完整。
2. **Given** 某个旧 tool 消息只有 50 字符（如 `{"result": "Updated 5 tasks"}`），**When** L2 判定它是否该被替换，**Then** 因为 ≤120 字符，跳过（保留原样）。
3. **Given** 对话中总共只有 3 个 tool 消息（≤5），**When** L2 运行时，**Then** 什么都不做。

---

### User Story 4 - 上下文仍然超限时自动生成 LLM 摘要 (Priority: P2)

当前三层压缩（L3+L1+L2）全部执行后，总消息大小仍然超过阈值（200,000 字符），说明对话已经非常长。此时必须以一次 API 调用的代价，让 LLM 将整个对话历史浓缩为一份精炼摘要，用一条摘要消息替代所有历史。

**Why this priority**: L4 是最后的自动防线，也是最贵的一步（1 API 调用）。它在 L1-L3 所有免费手段用尽后才触发。

**Independent Test**: 构造一段超过 200K 字符的模拟对话，验证 compact_history 被触发，保存 transcript 到 `.transcripts/`，调用 LLM 生成摘要，messages 被替换为 1 条摘要消息。

**Acceptance Scenarios**:

1. **Given** L3→L1→L2 全部执行完毕，消息总大小仍 >200,000 字符，**When** 管线进入 L4 判断，**Then** 系统保存完整对话到 `.transcripts/transcript_<timestamp>.jsonl`，调用 LLM 生成摘要（要求保留当前目标、关键发现、已改文件、剩余工作、用户约束），用摘要消息 `[Compacted]\n\n<summary>` 替代全部历史消息。
2. **Given** L4 正在执行 LLM 摘要生成，**When** 摘要生成完成，**Then** messages 列表从可能几百条缩减为 1 条（摘要消息），后续的 system prompt 和工具声明不受影响（它们通过 tools 参数单独发送）。
3. **Given** L3→L1→L2 执行后消息大小 ≤200,000 字符，**When** 管线判断是否触发 L4，**Then** L4 被跳过，直接继续正常 LLM 调用。

---

### User Story 5 - 压缩后恢复 Todo 列表，Agent 不丢失工作进度 (Priority: P2)

当 L4 触发后，整个对话历史被替换为一条摘要消息。但 Agent 可能正在执行一个多步骤计划（通过 todo_write 管理），这些 todo 的状态不能丢失。系统应在摘要消息之后自动注入当前 todo 列表作为上下文补充。

**Why this priority**: 状态连续性是压缩质量的关键。如果压缩后 Agent 忘了自己在做什么（todo 进度丢失），压缩的代价就是任务断裂。这是本项目唯一确定的 Post-Compact 恢复项。

**Independent Test**: 在 Agent 执行过程中设置 CURRENT_TODOS 有 3 个任务（1 pending, 1 in_progress, 1 completed），触发 L4 compact_history，验证压缩后的 messages 中包含了 todo 列表的格式化注入。

**Acceptance Scenarios**:

1. **Given** CURRENT_TODOS 中有 `[{status: "completed", content: "分析需求"}, {status: "in_progress", content: "编写代码"}, {status: "pending", content: "测试"}]`，**When** L4 压缩完成后，**Then** messages 中追加一条 user 消息，内容包含格式化的 todo 列表（含状态图标/标记）。
2. **Given** CURRENT_TODOS 为空列表，**When** L4 压缩完成后，**Then** 不注入 todo 恢复消息（无需恢复空列表）。
3. **Given** 压缩是 L1 或 L2 或 L3 触发的（非 L4），**When** 这些层执行完毕，**Then** 不触发 todo 恢复（消息历史未被整体替换，历史中仍有 todo_write 的调用记录）。

---

### Edge Cases

- 当 head 和 tail 的边界保护导致两者重叠时（head_end ≥ tail_start），L1 应跳过截断（所有消息都被"保护"了，没什么可裁的）。
- 当最新一轮的 tool 消息既有超大内容又有小内容时，L3 应优先持久化最大的，可能只需处理 1-2 条就能降到阈值以下。
- L4 生成的摘要本身不应超过合理范围（建议限制 LLM max_tokens=2000）。
- 管线执行顺序绝对不能改变：L3 必须在 L2 之前（L2 会把旧结果替换为占位符，L3 需要完整内容来判断大小）。
- 消息格式是 OpenAI 标准（role 为 "system"/"user"/"assistant"/"tool"），所有层的消息遍历逻辑必须适配此格式，不能照搬 Anthropic 格式（content block list）。
- L4 摘要消息使用 role="user"（而非 system），因为后续 LLM 调用需要它作为对话上下文的一部分。
- 压缩管线不应修改原始的 system prompt（第一条 system 消息），它始终通过 tools 参数保持独立。
- L4 摘要 LLM 调用失败（网络错误、API 限流等）时，重试 N 次后仍失败则跳过压缩降级运行——不阻塞 Agent 主流程。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须在每轮 LLM 调用前，自动按 L3→L1→L2→L4 顺序执行压缩管线。
- **FR-002**: L3 (tool_result_budget): 系统必须找到最近一轮 assistant tool_calls 对应的所有 role="tool" 消息，计算其 content 总字符数。当总大小超过 500,000 字节且存在单个结果的 content 超过 30,000 字符时，将超大的 tool 消息内容持久化到 `.task_outputs/tool-results/<tool_call_id>.txt`，并在消息中替换为包含文件路径和 2000 字符预览的 `<persisted-output>` 标记。若本轮无 tool 消息则跳过。
- **FR-003**: L3 必须按各 tool 消息 content 从大到小排序处理，每持久化一条后重新计算总量，直到降到阈值以下。只处理最近一轮 tool_calls 对应的消息，不处理历史消息（历史消息由 L2 负责）。
- **FR-004**: L1 (snip_compact): 系统必须在消息总数超过 100 条时，保留前 3 条和后 97 条，中间替换为 `[snipped N messages]` 占位符。N 为实际被裁掉的消息数量。
- **FR-005**: L1 必须保护 tool 调用配对：如果 head 最后一条是 assistant（含 tool_calls），则后续对应的 tool 结果消息必须一并保留；如果 tail 第一条是孤立的 tool 结果（对应的 assistant tool_calls 在前一条），则前一条 assistant 消息必须一并保留。如果保护逻辑导致 head 和 tail 重叠，则跳过截断。
- **FR-006**: L2 (micro_compact): 系统必须遍历所有 role="tool" 的消息，保留最新 5 个的完整内容（`tool_call_id` 和 `content` 不变），其余超过 120 字符的替换 `content` 为 `[Earlier tool result compacted. Re-run if needed.]`。
- **FR-007**: L2 对于 content ≤120 字符的旧 tool 消息不进行替换（小内容不值得压缩）。
- **FR-008**: L4 (compact_history): 系统必须在 L1-L3 执行后消息总大小仍超过 200,000 字符时触发。触发流程：(a) 保存完整对话到 `.transcripts/transcript_<timestamp>.jsonl`；(b) 构建摘要 prompt（要求保留当前目标、关键发现、已改文件、剩余工作、用户约束）；(c) 调用 LLM 生成摘要（max_tokens=2000），若失败则最多重试 N 次（N 可配置，默认 2）；(d) 成功后用 `[Compacted]\n\n<summary>` 消息替代全部历史消息；若全部重试失败，跳过压缩降级运行（保留原始消息继续后续 LLM 调用）。
- **FR-009**: Post-Compact 恢复：L4 压缩完成后，如果 CURRENT_TODOS 非空，系统必须追加一条 user 消息，将当前 todo 列表格式化为可读文本注入对话。
- **FR-010**: 各层阈值必须可通过配置调整：CONTEXT_LIMIT（字符数，默认 200,000）、MAX_MESSAGES_SNIP（消息数，默认 100）、TOOL_RESULT_BUDGET_BYTES（字节，默认 500,000）、PERSIST_THRESHOLD（字节，默认 30,000）、KEEP_RECENT_TOOL_RESULTS（数量，默认 5）。
- **FR-011**: 压缩管线必须作为 Agent 循环的有机组成部分，嵌入时机为每轮 LLM 调用前。不提供 compact 工具给模型手动调用。
- **FR-012**: 管线中的每一层在无触发条件时都是无操作（no-op），不产生副作用。
- **FR-013**: 仅 L4 触发时在终端输出一行提示（如 `[auto compact]`），告知用户发生了 LLM 摘要压缩。L1/L2/L3 静默运行，不产生终端输出。

### Key Entities

- **CompactedMessage**: 经过压缩管线处理后的消息列表，保留了原始消息的 role/tool_call_id 结构，但部分消息的 content 可能已被替换（占位符或摘要）。
- **TranscriptRecord**: L4 生成的完整对话存档（JSONL 格式），保存在 `.transcripts/` 目录，每条消息一行 JSON，供事后回溯。
- **PersistedToolResult**: L3 生成的大工具输出文件，保存在 `.task_outputs/tool-results/` 目录，文件名以 tool_call_id 命名，内容为原始完整输出。
- **CompactionConfig**: 压缩配置参数集，包含各层阈值，可从 config.py 统一管理。
- **TodoState**: 当前会话的任务列表状态（CURRENT_TODOS），L4 压缩后作为恢复注入的来源。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 在单次 Agent 运行中，即使执行了返回 1MB+ 输出的命令，Agent 也不会因上下文溢出而崩溃——系统自动将超大输出持久化并在对话中保留预览。
- **SC-002**: 当对话超过 150 条消息时，L1 能将消息数缩减到 ≤101 条，且不会破坏任何 tool_calls/tool 消息配对（API 不会因消息格式错误而拒绝请求）。
- **SC-003**: 当对话有 10 个以上 tool 消息时，L2 能将早期大型 tool 消息的内容替换为占位符，释放 ≥80% 的旧 tool 消息所占字符空间。
- **SC-004**: 当对话上下文超过 200,000 字符时，L4 能以 1 次 API 调用的代价生成摘要，将消息列表缩减为 1-2 条（摘要 + 可选的 todo 恢复），使 Agent 能继续工作。
- **SC-005**: L4 压缩后，Agent 能基于摘要消息和恢复的 Todo 列表继续执行任务，不会重复已完成步骤或遗漏待办步骤。
- **SC-006**: 压缩管线在不需要时完全透明——短对话（<50 条消息、<100K 字符）中不触发任何压缩，Agent 行为与无压缩时完全一致。

## Assumptions

- 本项目使用 OpenAI 兼容的消息格式（role: system/user/assistant/tool，通过 tool_call_id 关联配对），所有压缩逻辑基于此格式设计。
- token 估算采用字符数近似（`len(str(messages))`），不引入精确 tokenizer。对于中英文混合内容，2 字符 ≈ 1 token 是一个可接受的粗略估算。
- L4 摘要生成的 LLM 调用复用现有的 `LLMClient` 实例和模型配置（deepseek-v4-pro）。
- 压缩管线嵌入 Agent 循环，但不修改 `Agent.run()` 的核心 Think→Act→Observe 结构——通过提取独立的 compact 模块 + 在 run() 中注入调用点来实现。
- 不做跨 run 的会话持久化，每次 `agent.run()` 创建全新 messages 的行为保持不变。
- 工具结果以 JSON 字符串形式存储在 `content` 字段中，L2 和 L3 的大小判断基于 `len(content)`。
- `.task_outputs/tool-results/` 和 `.transcripts/` 目录在首次需要时自动创建。
