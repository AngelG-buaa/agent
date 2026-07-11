# Feature Specification: Task Tool & Sub-Agent

**Feature Branch**: `002-task-subagent-tool`

**Created**: 2026-07-11

**Status**: Draft

**Input**: User description: "我现在要对stage 2/project里面我的实习项目进行继续的迭代。迭代部分主要要加入task工具，新增sub agent的功能。主要可以参考一下这个网址的内容https://github.com/shareAI-lab/learn-claude-code/tree/main/s06_subagent"

**Reference**: https://github.com/shareAI-lab/learn-claude-code/tree/main/s06_subagent

## Clarifications

### Session 2026-07-11

- Q: Sub-Agent 应拥有哪些工具？ → A: 与主 Agent 完全一致（11 个工具），仅排除 `task` 工具自身。包括 todo_write、web_search、web_fetch、search_knowledge、calculator、get_time、read_chunk 等全部辅助工具。 **修正**：Sub-Agent 也排除 `todo_write` 工具——任务规划统一由主 Agent 负责，Sub-Agent 只负责执行。
- Q: Sub-Agent 与主 Agent 是否共享 Permission Session？ → A: 共享同一个 Session。主 Agent 和 Sub-Agent 使用同一套权限会话规则，任一方批准的会话级 ALLOW/DENY 在另一方也生效。整个对话只有一个信任边界。
- Q: Sub-Agent 的 SYSTEM prompt 应包含什么？ → A: 独立精简 Prompt，全新编写，仅包含：你是子代理的身份声明、直接执行任务不委派、返回结论性结果。不继承主 Agent 的通用行为准则和 TodoWrite 工作流指导。
- Q: Sub-Agent 每次工具调用在终端显示什么？ → A: 精简输出格式 `[sub] tool_name(key_param_summary)`，如 `[sub] read_file(src/main.py)`、`[sub] bash(grep -r pattern)`。不显示完整参数 JSON。
- Q: Sub-Agent 达到最大轮数限制时如何处理？ → A: 最后一轮前注入提醒。在 Sub-Agent 的第 30 轮 LLM 调用前，自动向消息列表注入系统提醒（"你已达到最大轮数限制，请基于已有信息给出当前最佳结论"），给 Sub-Agent 一次收尾的机会。若第 30 轮后仍未完成（仍在调工具），则强制终止并返回最后一条消息的文本内容。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent 将复杂子任务委派给 Sub-Agent 执行 (Priority: P1)

当主 Agent 面对一个复杂任务，其中包含可以独立完成的子任务时（如"搜索项目中所有使用某函数的文件并汇总"），主 Agent 通过 `task` 工具启动一个 Sub-Agent，将子任务的描述传递给它。Sub-Agent 独立完成子任务后，仅将最终结论返回给主 Agent。主 Agent 拿到结论后继续工作，其对话上下文不会被 Sub-Agent 的中间推理步骤污染。

**Why this priority**: 这是 task 工具的核心价值——上下文隔离。没有这个能力，Agent 在处理复杂任务时，中间步骤会填满上下文窗口，导致 Agent "遗忘"原始目标。这是本功能的 MVP。

**Independent Test**: 给 Agent 一个需要多文件搜索和汇总的任务，验证 Agent 是否通过 task 工具委派搜索子任务，且子任务的中间步骤不出现在主 Agent 的对话中，只有最终结论被返回。

**Acceptance Scenarios**:

1. **Given** 主 Agent 收到"找出项目中所有使用 async/await 的文件并汇总其用途"的指令，**When** Agent 决定委派搜索任务，**Then** Agent 调用 task 工具，传入子任务描述，Sub-Agent 启动并执行搜索，最终仅返回汇总结论给主 Agent。
2. **Given** Sub-Agent 正在执行子任务（如读取多个文件），**When** 子任务完成，**Then** Sub-Agent 的中间步骤（每次文件读取的原始内容）不出现在主 Agent 的消息历史中，仅最终文本结论被追加。
3. **Given** 主 Agent 收到 Sub-Agent 的结论，**When** 主 Agent 继续工作，**Then** 主 Agent 可以基于结论进行下一步操作（如追问、写入文件等）。

---

### User Story 2 - Sub-Agent 在安全约束下独立运行 (Priority: P1)

Sub-Agent 拥有独立的执行循环和工具集，但其工具调用仍然受权限系统约束。Sub-Agent 不能无限递归（不能自己再启动 Sub-Agent），且有最大执行轮数限制以防止失控。

**Why this priority**: 安全约束是 Sub-Agent 机制的基础保障。没有约束的 Sub-Agent 可能无限运行、递归生成子进程，消耗大量资源甚至造成安全风险。

**Independent Test**: 构造一个需要 Sub-Agent 调用受限工具的场景，验证权限检查是否仍然生效。验证 Sub-Agent 在达到最大轮数后是否正确终止。

**Acceptance Scenarios**:

1. **Given** Sub-Agent 需要执行一个需要用户审批的敏感操作（如写文件），**When** Sub-Agent 调用该工具，**Then** 权限检查机制被触发，用户被询问是否允许（与主 Agent 相同的审批流程）。
2. **Given** Sub-Agent 已执行了最大允许轮数（如 30 轮），**When** Sub-Agent 仍未完成任务，**Then** Sub-Agent 强制终止并返回当前已有的结论。
3. **Given** Sub-Agent 尝试调用 task 工具启动另一个 Sub-Agent，**When** 该调用发生，**Then** 调用被拒绝（Sub-Agent 的工具集中不包含 task 工具）。

---

### User Story 3 - 用户可观察 Sub-Agent 的执行状态 (Priority: P2)

当主 Agent 启动 Sub-Agent 时，用户能在终端/输出中看到 Sub-Agent 的启动和完成标记。Sub-Agent 的工具调用以明确的标识符（如 `[sub]` 前缀）与主 Agent 的调用区分开来，使用户能理解当前是谁在执行什么操作。

**Why this priority**: 可观测性是 Agent 系统可靠性的基础。用户需要知道 Sub-Agent 何时启动、在做什么、何时完成，才能信任整个系统的行为。

**Independent Test**: 触发 Sub-Agent 执行，检查终端输出中是否包含启动/完成标记和 `[sub]` 前缀。

**Acceptance Scenarios**:

1. **Given** 主 Agent 调用 task 工具启动 Sub-Agent，**When** Sub-Agent 开始执行，**Then** 终端输出显示 `[Subagent spawned]` 及子任务描述。
2. **Given** Sub-Agent 正在执行中并调用工具，**When** 工具被调用，**Then** 终端输出中以 `[sub]` 前缀标识该调用，与主 Agent 的 `🔧 调用工具:` 输出区分。
3. **Given** Sub-Agent 执行完毕，**When** Sub-Agent 返回结论，**Then** 终端输出显示 `[Subagent done]`。

---

### Edge Cases

- Sub-Agent 的任务描述为空或过于模糊时，Sub-Agent 应能要求澄清或返回明确的错误信息，而非静默失败。
- Sub-Agent 在第 30 轮收到提醒后仍未完成（继续调用工具而非给出文本回复）时，系统强制终止并返回最后一条消息的文本内容。不返回固定的"达到最大步数限制"文本。
- 主 Agent 在同一步中连续启动多个 Sub-Agent 时，每个 Sub-Agent 应有独立的上下文，互不干扰。
- Sub-Agent 执行过程中如果 LLM API 返回错误，错误应被捕获并以可读格式返回给主 Agent，不应导致整个 Agent 崩溃。
- Sub-Agent 调用不存在的工具时，应收到与主 Agent 相同的"未知工具"错误，而非静默跳过。
- 当 Sub-Agent 的所有工具调用都被权限系统拒绝时，Sub-Agent 应能识别此情况并返回说明。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 提供 `task` 工具，接受一个 `description` 参数（字符串类型），描述需要 Sub-Agent 完成的子任务。
- **FR-002**: 调用 `task` 工具时，系统 MUST 创建一个 Sub-Agent 实例，该实例拥有全新的、独立的对话上下文（消息列表），仅包含子任务描述作为初始用户消息。
- **FR-003**: Sub-Agent MUST 运行自己的 Agent 循环（Think → Act → Observe），与主 Agent 的循环结构一致。
- **FR-004**: Sub-Agent MUST 拥有与主 Agent 基本相同的工具集（包括 bash、read_file、write_file、edit_file、glob、read_chunk、web_search、web_fetch、search_knowledge、calculator、get_time），但 MUST NOT 拥有 `task` 和 `todo_write` 工具——任务规划统一由主 Agent 负责，Sub-Agent 不能再次委派子任务。
- **FR-005**: Sub-Agent MUST 有最大执行轮数限制（默认为 30 轮）。在第 30 轮 LLM 调用前，系统 MUST 自动向 Sub-Agent 消息列表注入提醒，要求其基于已有信息给出当前最佳结论。若第 30 轮后 Sub-Agent 仍在调用工具，则强制终止并返回最后一条消息的文本内容。
- **FR-006**: Sub-Agent 的工具调用 MUST 经过与主 Agent 相同的 PreToolUse 权限检查流程，不可绕过。权限会话（Session）MUST 在主 Agent 和 Sub-Agent 间共享——主 Agent 中用户批准的会话级 ALLOW/DENY 规则在 Sub-Agent 中同样生效，反之亦然。
- **FR-007**: Sub-Agent 完成执行后，MUST 仅将最终文本结论返回给主 Agent。Sub-Agent 的中间步骤（消息历史、工具调用详情）MUST NOT 出现在主 Agent 的对话上下文中。
- **FR-008**: 主 Agent 收到 Sub-Agent 的结论后，MUST 将其作为 `task` 工具的标准工具结果追加到自身的对话中，格式与 Agent 调用其他工具时收到的结果一致。
- **FR-009**: 系统 MUST 在 Sub-Agent 启动和完成时向终端输出可观测标记（启动时显示子任务描述，完成时显示完成状态）。
- **FR-010**: Sub-Agent 的工具调用输出 MUST 使用 `[sub] tool_name(key_param_summary)` 精简格式（如 `[sub] read_file(src/main.py)`、`[sub] bash(grep -r pattern)`），与主 Agent 的 `🔧 调用工具: name({完整参数})` 格式区分。不显示完整参数 JSON。
- **FR-011**: Sub-Agent 的 SYSTEM prompt MUST 为独立编写的精简版本，包含以下核心内容：(1) 身份声明——你是一个子代理（Sub-Agent），负责执行主 Agent 委派的具体子任务；(2) 行为准则——直接使用工具完成任务，不要再次委派；(3) 输出要求——返回结论性结果。Sub-Agent 的 SYSTEM prompt MUST NOT 继承主 Agent 的通用行为准则或 TodoWrite 工作流指导。
- **FR-012**: `task` 工具 MUST 以同步方式执行——主 Agent 等待 Sub-Agent 完成后才继续下一轮。V1 不包含异步/后台执行模式。

### Key Entities

- **Task 工具 (task)**: Agent 可调用的新工具，接收子任务描述字符串，返回 Sub-Agent 的最终文本结论。
- **Sub-Agent**: 由 task 工具创建的独立 Agent 实例。拥有全新的消息列表、独立的执行循环、与主 Agent 基本相同的工具集（排除 task 和 todo_write）。运行结束后仅返回文本结论，中间过程被丢弃。
- **Sub-Agent 执行上下文**: 包含独立的 system prompt（子代理专用）、受限的工具 schema 列表（排除 task、todo_write）、独立的消息列表、独立的执行轮数计数器。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 主 Agent 在需要多步独立子任务时，能够通过 task 工具委派，且主 Agent 的对话轮数不因子任务中间步骤而膨胀。
- **SC-002**: Sub-Agent 的中间工具调用细节不出现在主 Agent 的最终回答中，用户只看到基于 Sub-Agent 结论的整合结果。
- **SC-003**: Sub-Agent 在 30 轮执行限制内正常终止，不会出现无限循环。
- **SC-004**: Sub-Agent 的受限工具调用能被权限系统拦截，敏感操作仍需用户审批。
- **SC-005**: 现有工具（bash、read_file、write_file、edit_file、glob、todo_write 等）在加入 task 工具后功能不受影响。
- **SC-006**: 用户能在终端输出中清晰分辨主 Agent 和 Sub-Agent 的工具调用。

## Assumptions

- Sub-Agent 与主 Agent 使用相同的 LLM 配置（相同的 API key、base URL、model），不引入独立的 LLM 客户端配置。
- Sub-Agent 的工具集与主 Agent 基本一致（包括 web_search、web_fetch、search_knowledge 等 10 个工具），排除 task（防止递归委派）和 todo_write（任务规划统一由主 Agent 负责）。
- V1 仅支持同步 Sub-Agent（主 Agent 阻塞等待），异步/后台执行模式不在本次迭代范围内。
- Sub-Agent 的权限审批方式与主 Agent 一致——使用终端 input() 交互式审批，权限规则（会话 ALLOW/DENY）在主 Agent 和 Sub-Agent 间共享。
- Sub-Agent 的最大轮数限制（30 轮）为硬编码常量，不可由 Agent 或用户动态调整（V1 简化）。
- 本功能基于现有 Agent 架构（`agent/agent.py` 中的 `Agent` 类和 `run()` 循环），不引入新的 Agent 基类或大规模重构。
