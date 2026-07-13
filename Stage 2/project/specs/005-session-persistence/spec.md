# Feature Specification: Session 持久化

**Feature Branch**: `005-session-persistence`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description: "给 agent 项目添加 session 持久化功能。支持历史会话列表浏览、选择恢复（resume）、重命名、删除。存储格式使用 SQLite，每 session 一个数据库文件，存放在项目目录下的 .myagent/sessions/ 中。"

## Clarifications

### Session 2026-07-13

- Q: session 持久化的最核心场景是什么？ → A: 历史会话列表浏览 + 选择恢复 + 重命名 + 删除
- Q: session 文件存放位置？ → A: 项目目录内（.myagent/sessions/），每个项目会话隔离
- Q: 存储格式？ → A: SQLite，每 session 一个 .db 文件
- Q: Session ID 生成方式？ → A: UUID（uuid4）
- Q: Session 标题来源？ → A: 首条用户消息自动截取前 N 字符
- Q: Session 列表排序？ → A: 按 updated_at 降序
- Q: Session 生命周期边界？ → A: 每次启动 main.py 即新 session，Ctrl+C 退出即结束；启动后空对话（仅有 system 消息就退出）自动清理 session 文件
- Q: 持久化失败处理？ → A: 抛出异常，告知用户
- Q: 需要持久化哪些内容？ → A: user/assistant/system 消息、会话元数据（title, updated_at, message_count）、权限 session 级 allow 决策、Todo 列表状态。不需要：SubAgent 子会话、progress 消息、各条消息的 timestamp、parent_id
- Q: 消息持久化写入时机？ → A: 消息进入 messages 列表的入口处（每条立即写），与 compact 分离——compact 修改内存中的 messages，持久化的是原始完整消息
- Q: Todo 持久化写入时机？ → A: TodoWrite 工具内部，每次调用后立即写入
- Q: 权限持久化写入时机？ → A: PermissionEngine 产生 session 级 allow 决策时，只记 allow（在表里 = 已允许），不记 deny
- Q: 每轮 Agent loop 结束后写入？ → A: 不。消息在进入列表的入口处立即写，compact 修改的是内存副本，持久化的是原始完整消息
- Q: Tool_name 在 messages 表中是否冗余？ → A: 否。恢复时从 assistant 消息的 tool_calls JSON 反向查找，满足 3NF
- Q: parent_id 和消息级 timestamp 是否需要？ → A: 不需要。线性消息序列用 seq 即可
- Q: Session 列表 sorted by → A: updated_at 降序，只保留 updated_at 字段（不需要 created_at）
- Q: Resume 入口？ → A: CLI 参数 --resume 或 REPL 内 /resume 命令。行为：先保存当前 session → 展示历史列表 → 切换到选定 session
- Q: Session 删除？ → A: os.remove(".db")，删除前确认（"Are you sure? [y/N]"）
- Q: 架构方式？ → A: 新增独立的 SessionManager 类，main.py 启动时创建并通过构造参数注入到 Conversation。符合 Constitution IX（新能力通过外部编排层注入）和高内聚低耦合
- Q: 当 `--resume` 时没有任何历史 session，系统如何处理？ → A: 提示 "No saved sessions found." 并自动进入新 session（等价于不带 --resume 直接启动）
- Q: Session 创建时机？ → A: `Conversation.start()` 调用时创建（REPL 启动时），不论用户是否输入消息都创建。空对话退出时由 SessionManager 负责清理。创建和清理逻辑都封装在 SessionManager 内部
- Q: Session 列表 UI 交互格式？ → A: 交互式选择（箭头键上下移动 + 回车选中），选中后弹出操作选项（恢复/删除/重命名）。用标准库实现（Windows: msvcrt, Unix: termios/tty），不引入第三方依赖

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 会话自动持久化与退出恢复 (Priority: P1) 🎯 MVP

用户启动 Agent 进行对话，无论是正常退出（/exit）还是被中断（Ctrl+C），对话内容都被自动保存。下次启动时，用户可以通过 `--resume` 参数查看历史会话列表，选择一个恢复继续对话。恢复后，之前的所有消息、权限决策、Todo 列表状态均完整可用。

**Why this priority**: 这是持久化功能的核心价值——对话不再"退出即丢失"。没有这个能力，历史列表、重命名、删除等都无从谈起。

**Independent Test**: 启动 Agent → 进行一轮简单对话（如"帮我创建一个 hello.py"）→ 退出 → `python main.py --resume` → 选择刚才的 session → 输入"给 hello.py 加上 main 函数" → Agent 理解上下文正确编辑。验证恢复后对话连贯性。

**Acceptance Scenarios**:

1. **Given** Agent 刚启动新 session，**When** 用户输入"列出当前目录文件"并得到回答后正常退出，**Then** `.myagent/sessions/{uuid}.db` 文件存在，messages 表中有完整的 user 和 assistant 消息
2. **Given** 存在一个历史 session（包含一轮对话），**When** 用户执行 `python main.py --resume` 并选择该 session，**Then** Agent 恢复该 session 的全部消息，用户可继续追问且 Agent 理解上下文
3. **Given** 用户在某 session 中对 bash 工具选择了 session 级 allow，**When** resume 该 session 后再次触发 bash 工具，**Then** 权限检查直接放行，不重复询问
4. **Given** 用户在某 session 中 Agent 使用了 TodoWrite 创建了 5 个任务并完成 3 个，**When** 退出后 resume 该 session，**Then** Todo 列表状态与退出前一致（5 个任务，3 个已完成）
5. **Given** 用户启动 Agent 后不输入任何内容直接退出（对话只有 system 消息），**When** 退出时检查，**Then** 该 session 的 .db 文件被自动清理，不在历史列表中

---

### User Story 2 - Session 列表管理与操作 (Priority: P2)

用户可以通过 `--resume` 或 `/resume` 查看所有历史 session，列表显示每个 session 的标题（首条用户消息截取）和最近更新时间（按更新时间降序排列）。用户可以对列表中的 session 进行恢复、删除和重命名操作。

**Why this priority**: 列表管理是 P1 恢复功能的必要交互界面。没有清晰的列表和搜索，历史 session 多了之后无法找到目标。

**Independent Test**: 创建 3 个不同的 session（内容不同）→ `python main.py --resume` → 验证列表显示 3 个 session，标题正确，按更新时间排序。删除第 2 个 → 验证列表变为 2 个。重命名第 1 个 → 验证标题更新。

**Acceptance Scenarios**:

1. **Given** 项目中有 3 个历史 session（分别在 10:00、11:00、12:00 创建），**When** 用户执行 `python main.py --resume`，**Then** 列表按更新时间降序显示：12:00、11:00、10:00，每个显示标题和更新时间
2. **Given** 用户正在查看 session 列表，**When** 用户选择删除某个 session 并确认 "y"，**Then** 该 session 的 .db 文件被删除，列表刷新后不再显示
3. **Given** 用户正在查看 session 列表，**When** 用户选择删除某个 session 但确认时输入 "n"，**Then** 该 session 保持不变
4. **Given** 用户正在查看 session 列表，**When** 用户对某个 session 执行重命名操作并输入新标题，**Then** 列表中该 session 显示新标题
5. **Given** 新创建的 session 用户首条消息为"帮我写一个 Python 的 HTTP 服务器"，**When** 该 session 出现在列表中，**Then** 其标题自动截取为"帮我写一个 Python 的 HTTP 服务器"（或前 50 字符）

---

### User Story 3 - REPL 内会话切换 (Priority: P3)

用户在 REPL 对话过程中，可以输入 `/resume` 命令——系统先保存当前 session，然后展示历史 session 列表供选择，选定后切换到目标 session 继续对话。当前正在进行的对话不会丢失。

**Why this priority**: 这是体验增强——用户不需要退出重进就能切换 session。但 P1 + P2 的 `--resume` 入口已能覆盖核心需求。

**Independent Test**: 在 session A 中对话一轮 → 输入 `/resume` → 选择 session B → 验证 session B 的消息恢复正确 → 再次 `/resume` → 切换回 session A → 验证 session A 的状态保持。

**Acceptance Scenarios**:

1. **Given** 用户正在 session A 中对话（已对话 3 轮），**When** 用户输入 `/resume` 并选择切换到 session B，**Then** session A 的当前状态被保存，Agent 切换到 session B 的上下文
2. **Given** 用户从 session A 切换到 session B 后，**When** 用户再次 `/resume` 切换回 session A，**Then** session A 的 3 轮对话完整保留，Agent 能继续之前的讨论
3. **Given** 用户在 session A 中已对话若干轮但尚未达到触发 compact 的阈值，**When** 用户 `/resume` 切换走再切回来，**Then** 对话保持原样不受影响

---

### User Story 4 - 权限跨轮持久化 (Priority: P3)

与 User Story 1 中的权限恢复场景一致：用户在某 session 中对特定工具选择了 session 级 allow（等同于"本次会话始终允许"），该决策被持久化到 permissions 表。后续轮次和 resume 后均自动生效，不再弹权限确认。

**Why this priority**: 与 P1 的权限恢复密切相关。作为独立的持久化实体，permissions 表的设计需要单独验证。

**Independent Test**: 在某 session 中触发需要权限确认的工具 → 选择 allow → 退出 → resume → 再次触发同类型工具 → 验证无权限弹窗。

**Acceptance Scenarios**:

1. **Given** 用户在 session 中首次使用 bash 工具时选择了 allow，**When** 同一 session 中第二次使用 bash 工具，**Then** 不弹权限确认直接执行
2. **Given** 用户在 session 中 allow 了 read_file 工具后退出，**When** resume 该 session 后使用 read_file，**Then** 权限决策仍然生效，不弹确认
3. **Given** 用户在 session A 中 allow 了 bash 工具，**When** 切换到 session B 后使用 bash，**Then** session B 需要重新进行权限确认（权限不跨 session 共享）

---

### Edge Cases

- 启动时 `.myagent/sessions/` 目录不存在？（自动创建）
- Session 文件被外部手动删除后，列表如何表现？（跳过不存在的文件，打印警告）
- 同时运行两个 main.py 实例使用同一个 session？（各 session 独立 .db 文件，不冲突；但同一 session 不应被两个进程同时操作——这是用户的使用约定）
- Session 的 .db 文件损坏？（捕获 sqlite3.Error，提示用户该 session 已损坏，可选择删除）
- 首条用户消息为空或纯空白？（标题回退为 "Untitled" 或类似默认值）
- 消息中包含特殊字符（如单引号、NULL 字节）？（使用参数化查询，防止 SQL 注入）
- 一个 session 的 messages 数量极大（>10000 条）？（按 seq 索引排序，性能可接受；必要时可分页加载）
- 权限表中同一 (session_id, tool_name) 重复插入？（PRIMARY KEY 约束天然防止重复，INSERT OR IGNORE）
- 退出时空对话清理逻辑——什么算"空对话"？（messages 表中无 user 消息的记录。判断：`SELECT COUNT(*) FROM messages WHERE role = 'user'` = 0）
- 磁盘满时写入失败？（抛出异常，告知用户，不静默丢失数据）
- Ctrl+C 退出时正在执行持久化写入？（Python 的 SQLite 默认 WAL 模式 + 短事务，单条 INSERT 基本原子完成；但我们不应依赖此——应在关键写入区域用 signal handler 延迟退出）
- `--resume` 但没有任何历史 session？（提示 "No saved sessions found." 并自动进入新 session，等价于不带 --resume 直接启动）

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统必须在 `Conversation.start()` 调用时创建 session（生成 UUID + 创建 SQLite .db 文件），会话目录为 `.myagent/sessions/`。创建逻辑由 SessionManager 封装
- **FR-002**: 系统必须在每条消息进入 messages 列表时立即持久化到 SQLite（在 compact 修改内存副本之前），持久化角色包括 user、assistant、system
- **FR-003**: 系统必须在 TodoWrite 工具调用完成后立即持久化当前 Todo 列表状态
- **FR-004**: 系统必须在权限引擎产生 session 级 allow 决策时立即持久化该决策到 permissions 表
- **FR-005**: 系统必须在 session 正常退出时更新 session 元数据（updated_at、message_count、title）
- **FR-006**: 系统必须在退出时检测空对话（messages 表中无 user 消息），由 SessionManager 自动清理该 session 的 .db 文件
- **FR-007**: 系统必须支持 `--resume` CLI 参数，展示历史 session 列表（按 updated_at 降序），包含标题和更新时间
- **FR-008**: 系统必须支持 REPL 内 `/resume` 命令——先保存当前 session，再展示列表供切换
- **FR-009**: 用户必须能从 session 列表中选择恢复、删除（确认后）或重命名 session
- **FR-010**: Session 恢复时必须重建完整的消息序列（按 seq 排序）、权限决策和 Todo 状态
- **FR-011**: 系统必须在恢复权限决策后，使 session 级 allow 的工具在后续操作中自动放行
- **FR-012**: Session 标题必须自动取自首条用户消息的前 N（如 50）字符；若首条消息为空，回退为 "Untitled"
- **FR-013**: 删除 session 必须采用 `os.remove()` 删除 .db 文件，删除前必须要求用户确认（"Are you sure? [y/N]"）
- **FR-014**: 持久化写入失败时必须抛出异常告知用户，不得静默丢弃数据
- **FR-015**: 所有 SQL 操作必须使用参数化查询，防止 SQL 注入
- **FR-016**: Session 的 .db 文件不存在于磁盘时，列表必须跳过并打印警告

### Key Entities

- **Session**: 一次完整的 Agent 会话。关键属性：id（UUID）、updated_at（ISO 时间戳）、title（首条用户消息截取）、message_count（消息总数）。持久化为一个独立的 SQLite .db 文件
- **Message**: 对话中的一条消息。关键属性：id（UUID）、session_id、seq（序号，按产生顺序递增）、role（user/assistant/system）、content（消息正文）、tool_calls（assistant 消息中的工具调用 JSON）、tool_call_id（tool 消息对应的调用 ID）
- **Permission Decision**: 用户在 session 中对某个工具的允许决策。关键属性：(session_id, tool_name) 联合主键。只持久化 allow 决策——在表中 = 已允许
- **Todo State**: Agent 通过 TodoWrite 工具维护的任务列表。关键属性：(session_id, content) 联合主键、status（pending/in_progress/completed）、active_form（进行中的显示文案）

### Schema

```sql
-- 每 session 一个独立的 SQLite 数据库文件
sessions(id TEXT PRIMARY KEY, updated_at TEXT, title TEXT, message_count INTEGER)
messages(id TEXT PRIMARY KEY, session_id TEXT, seq INTEGER, role TEXT, content TEXT, tool_calls TEXT, tool_call_id TEXT)
permissions(session_id TEXT, tool_name TEXT, PRIMARY KEY(session_id, tool_name))
todos(session_id TEXT, content TEXT, status TEXT, active_form TEXT, PRIMARY KEY(session_id, content))
```

设计约束：
- `tool_name` 不冗余存储在 messages 中——恢复时从 assistant 消息的 `tool_calls` JSON 反向查找，满足 3NF
- `content` 和 `tool_calls` 以 JSON 字符串存储（TEXT 类型）
- 所有表严格满足 3NF

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户退出后重新 resume，消息恢复完整率 100%（所有消息按原始顺序恢复）
- **SC-002**: Session 列表加载时间 < 100ms（≤50 个 session）
- **SC-003**: 单条消息持久化写入时间 < 10ms（不影响 Agent 响应感知延迟）
- **SC-004**: 权限决策在 resume 后生效率 100%（之前 allow 的工具不再弹确认）
- **SC-005**: Todo 列表状态恢复完整率 100%（所有条目、状态、active_form 与退出前一致）
- **SC-006**: 空对话清理准确率 100%（有实际对话的 session 不被误删，空 session 不留残余）
- **SC-007**: 10 轮以上长对话触发 compact 后，恢复出的消息为 compact 前的原始完整消息
- **SC-008**: 删除确认机制：所有无确认输入（非 "y"）均不执行删除

## Assumptions

- 用户通过终端命令行与 Agent 交互，session 列表也是终端文本 UI 形式（非 GUI）
- SQLite 使用 Python 内置 `sqlite3` 模块，零额外依赖（符合 Constitution V）
- Session 文件存储在项目目录下的 `.myagent/sessions/`，自动创建目录（如不存在）
- 消息在进入 messages 列表的入口处持久化——而非每轮 loop 结束后。这确保 compact 修改内存副本时不影响已持久化的原始消息
- 首条用户消息截取长度默认为 50 字符
- v1 不持久化 SubAgent 子会话——仅主 Agent 对话
- v1 不持久化 progress 消息类型（它们是高频 UI 状态，不应进入持久化链）
- 权限只持久化 allow 决策（不需要 deny——不持久化 = 下次仍需确认）
- `--resume` 和 `/resume` 共享同一套 session 列表 UI 逻辑
