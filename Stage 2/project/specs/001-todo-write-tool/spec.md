# Feature Specification: TodoWrite Tool

**Feature Branch**: `001-todo-write-tool`

**Created**: 2026-07-11

**Status**: Draft

**Input**: 跟随 s05_todo_write 教程，为 Agent 添加 TodoWrite 工具，让 Agent 在执行复杂任务前能先规划步骤、在执行过程中跟踪进度。

**Tutorial Reference**: https://github.com/shareAI-lab/learn-claude-code/tree/main/s05_todo_write

## Clarifications

### Session 2026-07-11

- Q: 当 LLM 在一轮中返回纯文本（不调用任何工具）时，计数器是否也递增？ → A: 每轮都递增——无论 LLM 返回文本还是调用工具，只要未调用 todo_write 就计数。
- Q: SYSTEM Prompt 中 planning 指导的详细程度？ → A: 包含简洁工作流模板：先建 pending 列表 → 标记 in_progress → 逐个完成 → 标记 completed。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent 在执行复杂任务前规划步骤 (Priority: P1)

Agent 收到一个复杂的多步任务（如"重构一个 Python 文件：添加 type hints、docstring、main guard"），在动手之前先通过 todo_write 列出所有待完成的步骤。每个步骤以 `pending` 状态开始，Agent 逐个标记为 `in_progress` → 执行 → `completed`。

**Why this priority**: 这是 todo_write 工具的核心功能——让 Agent 具备规划能力。没有规划能力，Agent 面对复杂任务会丢失上下文、跳过步骤。

**Independent Test**: 给 Agent 发一个需要 3+ 步骤的任务，验证 Agent 是否在开始执行前调用了 todo_write 列出所有步骤，并在执行过程中更新任务状态。

**Acceptance Scenarios**:

1. **Given** Agent 收到"重构 Python 文件：添加 type hints、docstring、main guard"的指令，**When** Agent 开始执行，**Then** Agent 先调用 todo_write 创建至少 3 个 pending 任务，再逐个执行。
2. **Given** Agent 已有 todo 列表且正在执行第一个任务，**When** 第一个任务完成，**Then** Agent 调用 todo_write 将该任务标记为 completed，并将下一个任务标记为 in_progress。
3. **Given** Agent 完成了所有 todo，**When** 最后一个任务完成，**Then** 所有任务状态为 completed，Agent 给出最终回复。

---

### User Story 2 - 用户能看到 Agent 当前的任务进度 (Priority: P1)

当 Agent 调用 todo_write 后，任务列表以可视化格式显示在终端/输出中，用户可以一目了然地看到当前进度：哪些完成了、哪个正在进行、哪些还没开始。

**Why this priority**: 可观测性是 Agent harness 的基本要求。如果用户看不到 Agent 的规划，就无法信任 Agent 的行为。

**Independent Test**: 调用 todo_write 后检查终端输出是否包含带状态图标的任务列表。

**Acceptance Scenarios**:

1. **Given** Agent 调用了 todo_write 并传入了 3 个任务（状态分别为 pending、in_progress、completed），**When** todo_write 执行，**Then** 输出中显示 `[ ]`、`[▸]`、`[✓]` 三种图标区分状态，且内容可读。
2. **Given** Agent 多次调用 todo_write 更新任务列表，**When** 每次调用后，**Then** 显示的始终是最新的完整任务列表。

---

### User Story 3 - Agent 长时间未规划时被提醒 (Priority: P2)

当 Agent 连续多轮（3 轮）未调用 todo_write 时，系统自动注入一条提醒消息，提示 Agent 更新 todo 列表。这防止 Agent 在长对话中遗忘规划。

**Why this priority**: 提醒机制是规划工具的配套保障。没有提醒，Agent 可能在多轮对话后不再主动更新 todo，工具逐渐失效。

**Independent Test**: 模拟 Agent 连续 3 轮不调用 todo_write，验证第 4 轮前是否有提醒消息注入。

**Acceptance Scenarios**:

1. **Given** Agent 已连续 3 轮未调用 todo_write，**When** 第 4 轮 LLM 调用前，**Then** 消息列表中被注入一条 `<reminder>Update your todos.</reminder>`。
2. **Given** Agent 在收到提醒后调用了 todo_write，**When** 调用完成，**Then** 未调用计数器重置为 0，不再触发提醒。

---

### Edge Cases

- Agent 创建了一个空的 todo 列表（0 个任务）时，todo_write 应正常返回而不报错。
- Agent 两次连续调用 todo_write（中间没有执行其他工具）时，以最后一次调用为准。
- 非法的 status 值（不在 pending/in_progress/completed 中）应被拒绝并返回清晰的错误信息。
- todo_write 工具不能被用于文件操作——它不应该接受路径参数或执行任何 I/O。
- 当计数器在 max_steps 的最后一轮达到阈值时，提醒不注入（因为不再有下一轮 LLM 调用）。Agent 正常退出。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 提供 `todo_write` 工具，接受一个任务列表参数，每个任务包含 `content`（任务描述）和 `status`（状态）。
- **FR-002**: 状态 MUST 支持三种值：`pending`（待开始）、`in_progress`（进行中）、`completed`（已完成）。
- **FR-003**: todo_write 调用后 MUST 在终端/输出中以带状态图标的格式展示当前任务列表：[ ] pending、[▸] in_progress、[✓] completed。
- **FR-004**: 任务列表 MUST 存储在进程内存中（全局变量），随进程生命周期存在，不持久化到文件。
- **FR-005**: 系统 MUST 维护一个"未调用 todo_write 的轮数"计数器。每次 Agent 调用 todo_write 时重置为 0。
- **FR-006**: 当计数器达到 3 时，系统 MUST 在下一次 LLM 调用前向消息列表注入提醒。提醒格式为 `role: "user"`，内容为 `<reminder>Update your todos.</reminder>`。注入后计数器 MUST 重置为 0。
- **FR-007**: Agent 主循环的每一轮结束后，若该轮未调用 todo_write，计数器 MUST 递增 1（无论该轮 LLM 返回的是文本还是工具调用）。
- **FR-008**: todo_write MUST 注册为与其他工具（bash、read_file、write_file、edit_file、glob）同级别的标准工具。
- **FR-009**: todo_write 的 input_schema MUST 声明每个 todo 对象的 `content` 为必填字符串，`status` 为枚举类型。
- **FR-010**: Agent 的 SYSTEM prompt MUST 包含简洁的 todo_write 工作流指导：先列出所有步骤为 pending → 标记当前步骤为 in_progress → 完成该步骤 → 标记为 completed → 继续下一个 pending。不包含完整示例对话。

### Key Entities

- **Todo Item**: 单个任务项，包含 `content`（任务描述，字符串）和 `status`（状态，枚举：pending | in_progress | completed）。
- **Todo List**: 当前所有任务的有序集合，存储在进程内存中。每次 todo_write 调用整体替换。
- **Round Counter**: 记录连续未调用 todo_write 的轮数，用于触发提醒机制。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Agent 收到 3 步以上的复杂任务时，100% 在首次工具调用中包含 todo_write。
- **SC-002**: 用户可以通过终端输出清晰区分 pending、in_progress、completed 三种状态的任务。
- **SC-003**: 当 Agent 连续 3 轮忘记规划时，提醒机制在 3 轮内触发，不会漏掉。
- **SC-004**: todo_write 工具的加入不破坏现有 5 个工具（bash/read_file/write_file/edit_file/glob）的任何功能。

## Assumptions

- todo 数据存储采用进程内存方式（全局列表），与教程描述的 V1 版本一致，不引入文件持久化（V2 特性）。
- 提醒计数器在 Agent 主循环中维护（当前 `agent.py` 的 `run()` 方法内），而非在 todo_write 工具内部。
- 不使用 Claude Code 源码中的 `activeForm` 字段——教程明确说明教学版本省略了此字段。
- SYSTEM prompt 的修改是增量式的——在现有 prompt 基础上添加 planning 指导段落。
- 工具注册方式与现有 `tools/__init__.py` 中的 `register_all()` 模式一致。
