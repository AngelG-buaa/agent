# Feature Specification: Session 持久化（架构重修版）

**Feature Branch**: `007-session-persistence`

**Created**: 2026-07-14

**Status**: Draft

**Input**: User description: "基于新的权限架构（006-permission-refactor）重新设计 session 持久化方案。采用 Conversation → SessionController → SessionManager 三层结构替换旧的 Hook 驱动架构。新建、恢复、切换和退出必须具有唯一、可验证的生命周期。"

## Clarifications

### Session 2026-07-13 (from 005-session-persistence)

- Q: session 持久化的最核心场景是什么？ → A: 历史会话列表浏览 + 选择恢复 + 重命名 + 删除
- Q: session 文件存放位置？ → A: 项目目录内（.myagent/sessions/），每 session 一个 SQLite .db 文件
- Q: Session ID 生成方式？ → A: UUID（uuid4）
- Q: Session 标题来源？ → A: 首条用户消息自动截取前 N 字符（默认 50 字符）
- Q: Session 列表排序？ → A: 按 updated_at 降序
- Q: Session 生命周期边界？ → A: 每次启动 main.py 即新 session，退出即结束；空对话（无 user 消息）自动清理 session 文件
- Q: 持久化失败处理？ → A: 抛出异常，告知用户，不得静默丢弃数据
- Q: 需要持久化哪些内容？ → A: user/assistant/system 消息、会话元数据（title, updated_at, message_count）、权限 session 级 allow 决策（精确 grant，含 rule_content）、Todo 列表状态。不需要：SubAgent 子会话、progress 消息、各条消息的 timestamp、parent_id
- Q: 消息持久化写入时机？ → A: 消息进入 messages 列表的入口处（每条立即写），先写 SQLite 再追加到 working context。compact 修改的是内存副本，持久化的是原始完整消息
- Q: Resume 入口？ → A: CLI 参数 --resume 或 REPL 内 /resume 命令
- Q: 架构方式？ → A: Conversation → SessionController → SessionManager 三层结构。SessionController 拥有唯一的 active session 状态，SessionManager 是纯 SQLite Repository。main.py 只做参数解析和对象装配

### Architecture Decisions (2026-07-14)

- **权限架构**: 基于 006-permission-refactor 的实例级权限。PermissionEngine 提供 `set_grant_listener()` 和 `replace_session_rules()` 公开接口，不在 main.py 中访问 `_private_field`
- **消息协议**: Agent 提供 `on_message` 回调参数，主 session 由 SessionController 注入回调。恢复消息统一使用 OpenAI 兼容消息字典（仅含 role, content, tool_calls, tool_call_id 字段），不存在 tool_name 恢复字段
- **恢复语义**: "原始 transcript 重放"——从 SQLite 加载完整原始消息作为 working context，compact 管线按需重新压缩
- **生命周期状态机**: 稳定状态只有 NoActive 和 Active(session_id)，任意时刻最多一个 active session
- **Todo 隔离**: 新增 `snapshot_todos()` / `replace_todos()` 和 `TodoReminderHandle`，切换 session 时整体替换

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 会话自动持久化与退出恢复 (Priority: P1) 🎯 MVP

用户启动 Agent 进行对话，无论是正常退出（/exit）还是被中断（Ctrl+C），对话内容都被自动保存。下次启动时，用户可以通过 `--resume` 参数查看历史会话列表，选择一个恢复继续对话。恢复后，之前的所有消息、权限决策、Todo 列表状态均完整可用。

**Why this priority**: 这是持久化功能的核心价值——对话不再"退出即丢失"。没有这个能力，历史列表、重命名、删除等都无从谈起。

**Independent Test**: 启动 Agent → 进行一轮简单对话（如"帮我创建一个 hello.py"）→ 退出 → `python main.py --resume` → 选择刚才的 session → 输入"给 hello.py 加上 main 函数" → Agent 理解上下文正确编辑。验证恢复后对话连贯性。

**Acceptance Scenarios**:

1. **Given** Agent 刚启动新 session，**When** 用户输入"列出当前目录文件"并得到回答后正常退出，**Then** `.myagent/sessions/{uuid}.db` 文件存在，messages 表中有完整的 user 和 assistant 消息
2. **Given** 存在一个历史 session（包含一轮对话），**When** 用户执行 `python main.py --resume` 并选择该 session，**Then** Agent 恢复该 session 的全部消息，用户可继续追问且 Agent 理解上下文
3. **Given** 用户在某 session 中允许了 bash 工具的特定规则，**When** resume 该 session 后再次触发同规则匹配的 bash 调用，**Then** 权限检查直接放行，不重复询问
4. **Given** 用户在某 session 中 Agent 使用了 TodoWrite 创建了 5 个任务并完成 3 个，**When** 退出后 resume 该 session，**Then** Todo 列表状态与退出前一致（5 个任务，3 个已完成），Todo 提醒计数器从零开始
5. **Given** 用户启动 Agent 后不输入任何内容直接退出（对话无 user 消息），**When** 退出时检查，**Then** 该 session 的 .db 文件被自动清理，不在历史列表中
6. **Given** 用户在某 session 中使用了 SubAgent（Task 工具），**When** 退出后 resume 该 session，**Then** 主 session 的消息完整保留，SubAgent 的中间消息不出现在主 session 中
7. **Given** 恢复的 session 包含工具调用轮次，**When** 用户检查恢复后的消息，**Then** 消息按正确顺序排列：system → user → assistant(tool_calls) → tool → assistant(final)，且不存在 tool_name 额外字段

---

### User Story 2 - Session 列表管理与操作 (Priority: P2)

用户可以通过 `--resume` 或 `/resume` 查看所有历史 session，列表显示每个 session 的标题（首条用户消息截取）和最近更新时间（按更新时间降序排列）。用户可以对列表中的 session 进行恢复、删除和重命名操作。

**Why this priority**: 列表管理是 P1 恢复功能的必要交互界面。没有清晰的列表，历史 session 多了之后无法找到目标。

**Independent Test**: 创建 3 个不同的 session（内容不同）→ `python main.py --resume` → 验证列表显示 3 个 session，标题正确，按更新时间排序。删除第 2 个 → 验证列表变为 2 个。重命名第 1 个 → 验证标题更新。

**Acceptance Scenarios**:

1. **Given** 项目中有 3 个历史 session，**When** 用户执行 `python main.py --resume`，**Then** 列表按更新时间降序显示，每个显示标题和更新时间
2. **Given** 用户正在查看 session 列表，**When** 用户选择删除某个 session 并确认 "y"，**Then** 该 session 的 .db 文件被永久删除
3. **Given** 用户正在查看 session 列表，**When** 用户选择删除某个 session 但确认时输入 "n"，**Then** 该 session 保持不变
4. **Given** 用户正在查看 session 列表，**When** 用户对某个 session 执行重命名并输入新标题，**Then** 列表中该 session 显示新标题
5. **Given** 新创建的 session 无 user 消息，**When** 该 session 出现在列表中（作为启动阶段的空 session），**Then** 其标题回退为 "Untitled"
6. **Given** 用户正在 session 修复模式下查看列表（删除/重命名后），**When** 操作完成，**Then** 列表刷新显示最新状态；如果列表为空则提示并自动开始新 session

---

### User Story 3 - REPL 内会话切换 (Priority: P3)

用户在 REPL 对话过程中，可以输入 `/resume` 命令查看历史 session 列表，选定后切换到目标 session 继续对话。当前正在进行的对话不会丢失。

**Why this priority**: 这是体验增强——用户不需要退出重进就能切换 session。P1 + P2 的 `--resume` 入口已能覆盖核心需求。

**Independent Test**: 在 session A 中对话一轮 → 输入 `/resume` → 选择 session B → 验证 session B 的消息恢复正确 → 再次 `/resume` → 切换回 session A → 验证 session A 的状态保持。

**Acceptance Scenarios**:

1. **Given** 用户正在 session A 中对话（已对话 3 轮），**When** 用户输入 `/resume` 并选择切换到 session B，**Then** session A 的当前状态被完整保留（消息已逐条持久化），Agent 切换到 session B 的上下文
2. **Given** 用户从 session A 切换到 session B 后，**When** 用户再次 `/resume` 切换回 session A，**Then** session A 的 3 轮对话完整保留（包括工具调用的 assistant + tool 消息）
3. **Given** 用户在 session A 中已对话若干轮但尚未达到触发 compact 的阈值，**When** 用户 `/resume` 切换走再切回来，**Then** 对话保持原样不受影响
4. **Given** 用户在 `/resume` 列表中取消操作（按 Q），**When** 取消后，**Then** 当前 active session 保持不变，继续正常对话

---

### User Story 4 - 权限跨 Session 隔离 (Priority: P3)

与 User Story 1 中的权限恢复场景一致：用户在某 session 中对特定工具规则选择了 session 级 allow，该 grant（含精确 rule_content）被持久化。后续轮次和 resume 后均自动生效。不同 session 的权限严格隔离。切换 session 时旧 session 的权限规则被完整替换。

**Why this priority**: 基于 006-permission-refactor 的新权限架构，grant 精确到 (tool_name, rule_content)。权限隔离是 session 持久化的核心不变量之一。

**Independent Test**: 在 session A 中触发权限确认 → 选择"始终允许" → 退出 → resume → 再次触发同工具 → 验证无权限弹窗。切换到 session B → 触发同工具 → 验证需要重新确认。

**Acceptance Scenarios**:

1. **Given** 用户在 session 中首次触发需要确认的 bash 工具调用并选择"始终允许"，**When** 同一 session 中第二次触发匹配同规则的 bash 调用，**Then** 不弹权限确认直接执行
2. **Given** 用户在 session 中 allow 了某工具规则后退出，**When** resume 该 session 后触发匹配同规则的工具调用，**Then** 权限决策仍然生效，不弹确认
3. **Given** 用户在 session A 中 allow 了 bash 工具的某规则，**When** 切换到 session B 后触发 bash 工具，**Then** session B 需要重新进行权限确认（权限不跨 session 共享）
4. **Given** 用户恢复 session 时权限加载成功，**When** 加载后触发工具，**Then** 权限恢复不被视为新授权——不触发持久化回调

---

### Edge Cases

- 启动时 `.myagent/sessions/` 目录不存在？（自动创建）
- Session 文件被外部手动删除后，列表如何表现？（跳过不存在的文件，打印警告）
- Session 的 .db 文件损坏？（捕获错误，跳过后继续列出其他 session，打印警告告知用户可选择删除）
- 首条用户消息为空或纯空白？（标题回退为 "Untitled"）
- 消息中包含特殊字符（如单引号）？（使用参数化查询，防止 SQL 注入）
- 磁盘满时写入失败？（抛出异常，不得在持久化失败前修改内存状态）
- 退出时空对话清理逻辑——什么算"空对话"？（messages 表中无 role='user' 的记录）
- 连续中断退出（两次 Ctrl+C）？（必须经过同一个 `finally: close()` 路径确保清理）
- `--resume` 但没有任何历史 session？（提示 "No saved sessions found." 并自动进入新 session）
- `--resume` 时删除或重命名后列表为空？（刷新列表后若为空则自动开始新 session）
- 用户在 `/resume` 中选择当前 active session？（无副作用操作，不执行切换）
- resume/switch 过程中目标 session 加载失败？（保留原 active session 不变，不得进入半激活状态）
- active session 在运行期间被删除？（禁止删除当前 active session）
- 恢复时 permissions 表中 grant 的 rule_content 在策略文件中已不存在？（跳过该 grant，打印警告，不让整个恢复失败）
- 同一 session 被两个进程同时写入？（设计约定：不主动阻止，由 SQLite 文件锁提供基本保护；不实现跨进程协调）

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须在 Conversation 启动时创建新 session（生成 UUID + 创建 SQLite .db 文件），会话目录为 `.myagent/sessions/`。创建操作在一个事务内完成（schema 初始化 → sessions 行 → seq=0 的 system message），不可出现半成品状态
- **FR-002**: 系统必须在每条消息进入 working context 之前完成 SQLite 持久化（先写库、再追加到内存列表）。持久化失败时内存不得先行变化
- **FR-003**: 系统必须在 TodoWrite 工具调用完成后持久化当前 Todo 列表状态（含 position、status、active_form）。空 Todo 列表也必须持久化（覆盖旧状态）
- **FR-004**: 系统必须在权限引擎产生新 session 级 allow 决策时通过 listener 回调持久化该 grant（含 tool_name 和 rule_content）。恢复时通过 `replace_session_rules()` 原子替换，不触发 listener
- **FR-005**: 系统必须在每次消息写入时更新 session 元数据（updated_at、message_count），在首条 user 消息写入时自动设置 title
- **FR-006**: 系统必须在退出时检测空对话（messages 表中无 role='user' 的记录），自动清理该 session 的 .db 文件。正常退出和中断退出均经过同一个清理路径
- **FR-007**: 系统必须支持 `--resume` CLI 参数，展示历史 session 列表（按 updated_at 降序），包含标题和更新时间
- **FR-008**: 系统必须支持 REPL 内 `/resume` 命令，展示历史 session 列表供切换。当前 active session 保持不变直到目标 session 完整加载成功
- **FR-009**: 用户必须能从 session 列表中选择恢复、删除（确认后）或重命名 session。启动阶段（无 active session）可删除任意历史 session；运行期间禁止删除当前 active session
- **FR-010**: Session 恢复时必须在同一事务中加载完整快照（messages + permissions + todos），确保不拿到跨时点半份数据
- **FR-011**: 系统必须在恢复 session 后通过公开接口 `replace_session_rules()` 原子替换权限规则（目标为空即清空），并通过公开接口 `replace_todos()` 原子替换 Todo 列表（目标为空即清空）
- **FR-012**: Session 标题必须自动取自首条 user 消息的前 N（默认 50）字符；若首条消息为空，回退为 "Untitled"
- **FR-013**: 删除 session 必须物理删除 .db 文件，删除前必须要求用户确认（"Are you sure? [y/N]"）
- **FR-014**: 持久化写入失败时必须抛出异常告知用户，不得静默丢弃数据
- **FR-015**: 所有 SQL 操作必须使用参数化查询，防止 SQL 注入。所有 session id 传入文件操作前必须通过 UUID 校验和路径越界检查
- **FR-016**: Session 的 .db 文件不存在于磁盘时，列表必须跳过并打印警告。损坏数据库跳过并警告，Windows 下不得残留文件锁
- **FR-017**: 系统必须在 session 创建和恢复后重置 Todo 提醒计数器（reminder 从零开始）。切换 session 时非活跃 session 的提醒不活跃
- **FR-018**: resume/switch 过程中任何步骤失败（加载、校验、权限替换、Todo 替换），系统必须保留原 active session 不变，不进入"messages 仍在但 active id 已清空"的半激活状态
- **FR-019**: 切换到当前 active session 必须是幂等无副作用操作
- **FR-020**: 恢复后的消息必须只包含 role、content、tool_calls、tool_call_id 四个字段，不得出现 tool_name 字段或非可序列化对象
- **FR-021**: SubAgent（Task 工具）的中间消息（system、user、assistant、tool）必须仅存在于子上下文，不出现在主 session 的持久化存储中

### Key Entities

- **Session**: 一次完整的 Agent 会话。关键属性：id（UUID）、updated_at（ISO 8601 UTC 时间戳）、title（首条 user 消息截取）、message_count。持久化为 `.myagent/sessions/{uuid}.db`
- **Message**: 对话中的一条消息。关键属性：id（UUID）、session_id、seq（按产生顺序递增）、role（system/user/assistant/tool）、content（消息正文，可 null）、tool_calls（assistant 消息的 JSON，可 null）、tool_call_id（tool 消息的调用 ID，可 null）。不存在 tool_name 冗余字段
- **Permission Grant**: 用户在某 session 中授予的一条精确权限。关键属性：tool_name + rule_content（联合自然键，精确对应策略中的一条 ASK 规则）。只持久化 allow 决策
- **Todo State**: Agent 通过 TodoWrite 工具维护的任务列表。关键属性：position（列表序号，保留顺序）、content（任务描述）、status（pending/in_progress/completed）、active_form（进行中的显示文案）
- **Session Snapshot**: 恢复时使用的完整数据快照，包含 session 元数据 + 全部 messages + 全部 permission grants + 全部 todos，在同一事务中读取

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户退出后 resume，消息恢复完整率 100%（所有消息按原始 seq 顺序恢复，含工具调用的完整链）
- **SC-002**: Session 列表加载时间 < 100ms（≤50 个 session 时）
- **SC-003**: 单条消息持久化写入时间 < 10ms（不影响 Agent 响应感知延迟）
- **SC-004**: 权限 grant 在 resume 后生效率 100%（之前在 session 中 allow 的规则不再弹确认）
- **SC-005**: Todo 列表状态恢复完整率 100%（所有条目的 content、position、status、active_form 与退出前一致）
- **SC-006**: 空对话清理准确率 100%（有实际 user 消息的 session 不被误删，空 session 不留残余文件）
- **SC-007**: 权限跨 session 隔离正确率 100%（session A 的 grant 在 session B 中不生效）
- **SC-008**: SubAgent 中间消息不出现在主 session 持久化中的正确率 100%
- **SC-009**: resume/switch 失败时原 active session 保持不变的可靠率 100%
- **SC-010**: 恢复后的消息字段合规率 100%（只含 role/content/tool_calls/tool_call_id，无 tool_name 或 SDK object）

## Assumptions

- 用户通过终端命令行与 Agent 交互，session 列表也是终端文本 UI 形式（非 GUI）
- SQLite 使用 Python 内置 `sqlite3` 模块，零额外依赖（符合 Constitution V）
- Session 文件存储在项目目录下的 `.myagent/sessions/`，自动创建目录（如不存在）
- 消息在进入 working context 之前持久化——先写 SQLite 再追加到内存，确保 compact 修改内存副本时不影响已持久化的原始消息
- 首条用户消息截取长度默认为 50 字符
- v1 不持久化 SubAgent 子会话——仅主 Agent 对话
- v1 不持久化 progress 消息类型（它们是高频 UI 状态，不应进入持久化链）
- 权限只持久化 allow grant（在表中 = 已允许），不记录 deny 决策
- `--resume` 和 `/resume` 共享同一套 session 列表 UI 逻辑
- 不支持同时运行多个 active session（单 session CLI 模型）
- 不保存 compact 后的 working context checkpoint；恢复时以原始 transcript 重新构建上下文，compact 管线按需重新压缩
- 恢复后的消息统一满足 OpenAI 兼容接口的消息字典契约（role, content, tool_calls, tool_call_id）
- SubAgent 与主 Agent 共享同一个 executor 和 permission engine，因此共享当前 active session 的权限（不共享消息持久化出口）
- 权限的精确 grant（tool_name + rule_content）恢复策略：rule_content 在当前策略文件中找不到对应规则时，跳过该 grant 并警告，不让整个恢复失败
