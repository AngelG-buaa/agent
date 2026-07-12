# Feature Specification: 交互式对话

**Feature Branch**: `004-interactive-conversation`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "实现用户与Agent的多轮交互对话能力，以及Agent在执行过程中可以反问用户的功能。包括：1. 多轮对话——用户完成一轮对话后可以继续追问，Agent保留上下文记忆，而非程序退出。2. Agent反问用户——Agent在执行过程中遇到不确定信息时，可调用ask_user工具向用户提问，用户回答后Agent继续执行。"

## Clarifications

### Session 2026-07-12

- Q: 当用户对 ask_user 的提问给出无效回答（如"不知道"、"随便"），Agent 应如何处理？ → A: Agent 应基于已有信息给出最佳猜测并继续执行，将用户的无效回答视为"你自己决定"的授权
- Q: 用户退出时，系统应如何处理未完成的 TodoWrite 任务？ → A: 直接退出，提示用户还有未完成任务但丢弃未完成状态（v1 不持久化会话）
- Q: ask_user 提问时终端应显示多少上下文信息？ → A: 由 LLM 自行决定，通过 system prompt 引导 Agent 在问题中附带足够的背景信息（如当前在做什么、为什么需要问），系统层面不硬编码展示格式

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 多轮连续对话 (Priority: P1) 🎯 MVP

用户启动 Agent 后，可以像聊天一样与 Agent 进行多轮对话。每轮对话中，Agent 可以调用工具完成任务并给出回答；回答完成后，用户可以继续追问、提出新需求，Agent 能记住之前的对话上下文。只有当用户明确退出（如输入 `/exit` 或 Ctrl+C）时程序才结束。

**Why this priority**: 多轮对话是交互式体验的基础设施。没有它，每次对话都是"一次性"的，用户无法追问、无法连续工作。AskUser 反问功能也依赖此基础。

**Independent Test**: 启动 Agent，连续输入 3 个相关但不完全相同的问题（如"列出项目文件"→"第一个文件的内容是什么"→"它有多少行"），验证 Agent 能正确理解上下文指代并给出连贯回答。

**Acceptance Scenarios**:

1. **Given** Agent 刚启动，**When** 用户输入"帮我创建一个 hello.py"，**Then** Agent 创建文件并回复完成，然后显示输入提示符等待用户下一条输入
2. **Given** Agent 刚完成"创建 hello.py"的任务，**When** 用户输入"给它加上 main 函数"，**Then** Agent 理解"它"指的是 hello.py，正确编辑该文件
3. **Given** 对话已进行 5 轮以上，**When** 用户输入"还记得我最开始让你创建的文件吗？"，**Then** Agent 能回顾上下文并正确回答（如上下文已被 compact，则应给出合理的摘要结论）
4. **Given** 任意时刻，**When** 用户输入 `/exit` 或按下 Ctrl+C，**Then** Agent 优雅退出，显示告别信息

---

### User Story 2 - Agent 主动向用户提问 (Priority: P2)

Agent 在执行任务过程中，遇到信息不足或歧义时，可以主动调用 `ask_user` 工具向用户提问，而不是靠猜测继续。用户回答后，Agent 将答案纳入上下文，继续执行剩余步骤。

**Why this priority**: 这是"交互式"的核心价值——Agent 不再是黑盒执行，而是可以与用户协作。但这个功能依赖 P1 的对话基础设施。

**Independent Test**: 给 Agent 一个模糊指令（如"帮我改一下那个文件"而不指定具体文件），观察 Agent 是否会反问用户"你想修改哪个文件？"，用户回答后 Agent 能否正确定位并修改。

**Acceptance Scenarios**:

1. **Given** 用户输入"帮我分析一下那个数据"，**When** 上下文中有多个可能的数据文件且 Agent 无法确定，**Then** Agent 调用 ask_user 工具列出候选并请用户选择
2. **Given** Agent 调用了 ask_user 提问，**When** 用户输入回答，**Then** Agent 将回答作为工具结果接收，继续执行原任务
3. **Given** Agent 正在执行包含多个步骤的复杂任务，**When** 某步骤需要用户偏好判断（如"用 JSON 还是 YAML 格式？"），**Then** Agent 暂停该步骤等待用户回答，回答后继续后续步骤
4. **Given** Agent 调用了 ask_user，**When** 用户的回答足够清晰，**Then** Agent 不应就同一问题再次提问（避免重复询问）

---

### User Story 3 - 对话状态保持与恢复 (Priority: P3)

用户的对话状态（包括已批准的工具权限、TodoWrite 任务列表）在多轮对话中得以保持。例如用户在第三轮授予的"始终允许读文件"权限，在第四轮仍然生效；未完成的任务列表在下一轮对话中仍然可见。

**Why this priority**: 这是体验细节，提升连续工作的效率。但 P1 和 P2 已能独立交付价值。

**Independent Test**: 在一轮对话中通过权限审批选择"始终允许"，在下一轮中触发同类工具调用，验证不再重复询问。

**Acceptance Scenarios**:

1. **Given** 用户在第 N 轮通过权限审批选择了"始终允许"某操作，**When** 第 N+1 轮 Agent 执行同类操作，**Then** 不再询问用户，直接放行
2. **Given** 第 N 轮 Agent 使用了 TodoWrite 规划了 5 个步骤并完成了 3 个，**When** 第 N+1 轮用户继续相关任务，**Then** Agent 能看到之前的进度并继续剩余步骤
3. **Given** 对话持续了多轮，**When** 上下文超过限制触发了 compact，**Then** compact 后 Agent 仍然记得关键的任务状态和用户偏好

---

### Edge Cases

- 用户连续按多次 Ctrl+C 会发生什么？（应安全退出，不丢数据）
- 用户在 Agent 等待权限确认时按 Ctrl+C？（应取消当前操作，回到输入状态）
- Agent 调用了 ask_user 后用户一直不回答？（应有超时或用户可跳过）
- Agent 调用了 ask_user 但用户给出无效回答（如"不知道"、"随便"、"你自己看着办"）？（Agent 应将此视为授权，基于已有信息给出最佳猜测并继续执行，不应陷入追问循环）
- 用户输入为空（直接按回车）？（应忽略，重新显示提示符）
- Agent 连续多次调用 ask_user 形成"追问循环"？（应在 system prompt 中限制，最多连续 2-3 轮未获得有效信息后应给出最佳猜测）
- 跨轮对话中，compact 压缩掉了用户最初的核心需求？（应保留摘要，确保关键信息不丢失）
- 用户在 Agent 工具执行过程中输入新的需求？（应排队等待当前执行完成，还是立即中断？——v1 方案：等待当前轮完成后再处理新输入）
- 用户退出时 TodoWrite 中还有未完成任务？（提示用户后直接退出，v1 不持久化未完成状态）
- 用户退出时 Agent 正在执行工具调用？（等待当前工具调用完成后退出，不中断执行中的工具）

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须支持外层 REPL 循环——Agent 回答完成后，程序不退出，等待用户下一条输入
- **FR-002**: 系统必须在多轮对话间保留完整消息历史（messages），使 Agent 能引用之前的对话内容
- **FR-003**: 系统必须仅在首轮对话插入 system prompt，后续轮次复用已有上下文
- **FR-004**: 系统必须在每轮开始前运行 context compact 检查，防止跨轮对话超出上下文窗口
- **FR-005**: 系统必须提供明确的退出机制（如 `/exit` 命令或 Ctrl+C）
- **FR-006**: 系统必须新增 `ask_user` 工具，Agent 可通过该工具向用户提问并获取回答
- **FR-007**: `ask_user` 工具必须阻塞等待用户输入，用户回答后以 tool result 形式注入对话
- **FR-008**: System prompt 必须包含引导规则——明确 Agent 在何种情况下应反问用户、何种情况下不应反问，以及提问时应在问题中附带足够的背景信息（如当前在做什么、为什么需要用户输入、可选的选项等），使用户无需查看对话历史即可理解问题
- **FR-009**: 多轮对话间必须保持权限会话状态（session-level allow/deny 规则不因轮次切换而丢失）
- **FR-010**: TodoWrite 任务列表必须在多轮对话间持续可见，compact 后不被丢弃
- **FR-011**: 用户可通过空输入（直接回车）跳过，系统不应报错
- **FR-012**: `max_steps` 必须每轮重置，不跨轮累计
- **FR-013**: 当用户对 `ask_user` 给出无效回答（如"不知道"、"随便"）时，Agent 必须基于已有信息给出最佳猜测并继续，不应陷入追问循环

### Key Entities

- **Conversation Session**: 一次完整的对话会话，包含多轮用户输入和 Agent 回答。关键属性：消息历史列表、权限会话规则、任务列表状态、会话开始时间
- **Agent Turn**: 一轮 Agent 执行（对应一次用户输入到 Agent 回答完成）。关键属性：本轮步数计数、是否触发了 ask_user、是否触发了 compact
- **AskUser Request**: Agent 向用户发起的提问。关键属性：问题内容、上下文（Agent 正在做什么）、用户回答、时间戳

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户能在同一会话中连续进行 ≥10 轮对话，每轮 Agent 均能正确理解上下文指代
- **SC-002**: 当 Agent 遇到歧义指令时，≥70% 的情况下能主动反问用户而非猜测（通过 10 个含歧义的测试用例衡量）
- **SC-003**: Agent 反问用户后，收到回答能继续完成原任务的成功率 ≥90%
- **SC-004**: 跨轮 conversation 的总 token 消耗与单次长任务相比，增幅 ≤20%（因 system prompt 首轮插入和 compact 摘要保留）
- **SC-005**: 用户在对话中的权限选择（如"始终允许"）在后续轮次中 100% 生效，不出现重复询问
- **SC-006**: 用户从启动到完成第一个有效问答的体验与现有单次模式一致，无额外等待时间

## Assumptions

- 用户通过终端命令行与 Agent 交互，交互界面基于文本输入/输出（非 GUI）
- 退出机制采用 `/exit` 命令，同时保留 Ctrl+C 作为强制退出方式
- `ask_user` 工具采用同步阻塞模式（类似现有 `terminal_approver` 的 `input()` 方式），v1 不支持非阻塞/异步提问
- 用户在 Agent 工具执行过程中输入的新需求，v1 方案为等待当前轮完成后处理（非实时中断）
- 现有 LLM API（DeepSeek V4 Pro）和工具系统保持不变，不引入新的外部依赖
- 跨轮 `max_steps` 每轮重置为 50，不跨轮累计
- v1 不实现会话持久化（save/resume），退出即丢弃会话状态，后续迭代再考虑
