# Feature Specification: Permission 系统实例级重构

**Feature Branch**: `006-permission-refactor`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description: "Permission 部分重构：将 PermissionEngine 从全局 Hook 中拆出，改为 ToolExecutor 实例级注入；建立公开 grant/listener 接口，为后续 SessionController 重构做准备。"

**关联设计文档**: [docs/permission-executor-architecture.md](../../docs/permission-executor-architecture.md)

## Clarifications

### Session 2026-07-13

以下决策通过 19 轮采访确认：

- Q: fallback ASK（`matched_rule=None`）时用户选择 "session" → A: 降级为单次 allow。工具正常执行，不创建 grant，`NonPersistablePermission` 被 ToolExecutor 捕获后静默放行。

- **范围边界**: 完整 Permission 改造，包括实例级注入、移除全局权限 Hook、grant/listener 公开接口、双 executor 隔离验证。SessionController 重构不在本次范围内。
- **approver 归属**: ToolExecutor 构造参数，不随 engine 走。
- **PermissionGrant**: 纯数据契约 `(tool_name, rule_content)`，不含 condition 闭包。`(tool_name, rule_content)` 是稳定自然键。
- **listener 语义**: 仅在用户交互产生新 grant 时触发（持久化），恢复/切换/清空不触发。删除 `notify` 参数，删除 `clear_session_rules()`（用 `replace_session_rules([])` 替代）。
- **revoke**: 删除 `revoke_session_rule()`。全量替换是唯一状态修改路径。
- **fallback ASK**: `matched_rule=None` 时拒绝创建持久 grant，降级为单次 allow。
- **allow_for_session 签名**: `allow_for_session(result: EvalResult) -> PermissionGrant`。
- **replace 原子性**: 先构建候选集合，全部成功后再替换；任一无效则整体失败，保留原 `_session_rules` 不变。
- **EvalResult.rule** → **EvalResult.matched_rule**，语义明确。
- **_collect_all_rules**: 显式覆盖逻辑——DENY 始终保留，SESSION ALLOW 覆盖同 key 的 POLICY ASK。
- **内部索引**: `_policy_rules_by_key: dict[tuple[str,str], PermissionRule]`，初始化时构建，重复 key 报错。
- **异常类型**: `tooling/permission/exceptions.py`，公开导出。
- **工厂函数**: 删除 `build_tool_executor()`，保留 `create_engine()`。`main.py` 显式组装。
- **非权限 Hook**: 保持全局 PreToolUse/PostToolUse 不动，仅从中移除权限 callback。
- **测试**: 重写权限模块测试，覆盖新接口 + 双 executor 隔离。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - ToolExecutor 实例隔离，互不干扰 (Priority: P1)

两个 ToolExecutor 实例（以不同 PermissionEngine 和 Approver 构造）在同一进程中运行时，各自的权限决策完全隔离。executor A 的 deny 策略不会阻止 executor B 执行工具。

**Why this priority**: 这是本次重构的根因。当前全局 Hook 注册导致两个 executor 的权限 callback 互相干扰。修复这个问题是本次重构存在的理由。

**Independent Test**: 创建 executor A（使用 deny-all engine + allow approver）和 executor B（使用 allow-all engine + allow approver）。executor B 执行工具应成功，不受 executor A 的 deny engine 影响。

**Acceptance Scenarios**:

1. **Given** executor A 绑定了 default_behavior="deny" 的 PermissionEngine，executor B 绑定了 default_behavior="allow" 的 PermissionEngine，**When** executor B 执行 `calculator(expression="1+1")`，**Then** 返回成功结果，不被 executor A 的 deny 策略阻断。
2. **Given** executor A 和 executor B 各自有独立的 PermissionEngine，**When** executor A 中通过 approver 创建一条 session grant，**Then** executor B 不受该 grant 影响（两个 engine 的 `_session_rules` 完全隔离）。
3. **Given** 同一个 executor 被主 Agent 和 SubAgent 共享，**When** SubAgent 执行工具调用，**Then** 使用与主 Agent 相同的 PermissionEngine 和 session rules（共享 executor = 共享权限）。

---

### User Story 2 - 权限检查从全局 Hook 移到 ToolExecutor 实例内部 (Priority: P1)

`ToolExecutor.execute()` 在查找工具后、触发非权限 PreToolUse Hook 前，调用本实例的 `PermissionEngine.evaluate()` + 必要时 Approver。权限决策不再依赖全局 `HOOKS["PreToolUse"]` 中注册的 callback。

**Why this priority**: 与 US-1 互为表里。US-1 是结果（隔离），US-2 是手段（从全局移到实例）。两者共同构成重构的核心。

**Independent Test**: 注册一个全局 PreToolUse hook（如日志），验证 ToolExecutor 执行时先完成权限检查，再触发该 hook。在全局 HOOKS["PreToolUse"] 中确认没有 PermissionEngine 相关 callback。

**Acceptance Scenarios**:

1. **Given** ToolExecutor 以 PermissionEngine(default_behavior="deny") 构造，**When** 调用 `executor.execute("bash", {"command": "ls"})`，**Then** 返回权限错误 dict，不触发任何全局 PreToolUse Hook（因为权限检查在实例内部、全局 Hook 之前阻断）。
2. **Given** ToolExecutor 以 PermissionEngine(default_behavior="allow") 构造，全局注册了一个 PreToolUse 日志 hook，**When** 调用 `executor.execute("bash", {"command": "echo hello"})`，**Then** 先通过权限检查，再触发全局 PreToolUse hook，最后执行工具。
3. **Given** 生产代码（main.py）已完成组装，**When** 搜索 `register_hook("PreToolUse"` 调用，**Then** 不存在传入 PermissionEngine callback 的代码路径。

---

### User Story 3 - 用户选择"始终允许"触发 grant 持久化通知 (Priority: P1)

当 ASK 规则命中且用户通过 Approver 选择"始终允许"（session），Engine 创建 `PermissionGrant` → 调用 listener 持久化 → 安装运行时规则。此后同一 `(tool_name, rule_content)` 的操作不再询问。

**Why this priority**: 这是 SessionController 重构的前置接口。listener 机制是权限系统和持久化层之间的契约。

**Independent Test**: 安装 listener，通过 `allow_for_session()` 创建 grant，验证 listener 被调用一次且收到正确的 `PermissionGrant(tool_name, rule_content)`。

**Acceptance Scenarios**:

1. **Given** Engine 安装了 grant_listener，ASK 规则 `(bash, "rm *")` 命中，用户通过 Approver 选择 "session"，**When** `_authorize_tool_call` 调 `engine.allow_for_session(result)`，**Then** listener 被调用一次，参数为 `PermissionGrant(tool_name="bash", rule_content="rm *")`，随后 `engine.evaluate("bash", {"command": "rm file.txt"})` 返回 ALLOW。
2. **Given** Engine 安装了 grant_listener，**When** 调用 `replace_session_rules(grants)`，**Then** listener 不被触发。
3. **Given** Engine 安装了 grant_listener，**When** 调用 `replace_session_rules([])`，**Then** listener 不被触发。
4. **Given** 用户对 fallback ASK（`matched_rule=None`）选择 "session"，**When** `allow_for_session(result)` 被调用，**Then** 抛出 `NonPersistablePermission`，不触发 listener，不安装内存规则。

---

### User Story 4 - Session grants 的全量恢复与替换 (Priority: P2)

SessionController（未来实现）通过 `replace_session_rules(grants)` 恢复历史会话的权限授权集。Engine 根据 `PermissionGrant` 的 `(tool_name, rule_content)` 找到对应策略规则，重建运行时 `PermissionRule`（复用 condition），原子性替换当前 `_session_rules`。

**Why this priority**: 这是 SessionController 与 PermissionEngine 之间的核心协作接口。当前先实现 Engine 侧，SessionController 在后续迭代中接入。

**Independent Test**: 预先在 Engine 中通过 `allow_for_session()` 创建 grant A，调用 `replace_session_rules([grant_a, grant_b])` 替换为 `[grant_a, grant_b]`，验证只有 B 对应的操作被放行。

**Acceptance Scenarios**:

1. **Given** Engine 的策略规则中包含 `(bash, "rm *")` 的 ASK 规则，**When** 调用 `replace_session_rules([PermissionGrant("bash", "rm *")])`，**Then** 此后 `evaluate("bash", {"command": "rm file.txt"})` 返回 ALLOW。
2. **Given** Engine 当前有 session rule A，**When** 调用 `replace_session_rules([grant_b])`，**Then** rule A 被移除，只有 rule B 生效（全量替换，不是增量追加）。
3. **Given** `replace_session_rules()` 的 grants 列表中包含一个 `(tool_name, rule_content)` 在当前策略规则中不存在的 grant，**When** 调用该方法，**Then** 抛出 `InvalidPermissionGrant`，原有 `_session_rules` 保持不变。
4. **Given** grants 列表包含重复的 `(tool_name, rule_content)` key，**When** 构建候选集合，**Then** 后者覆盖前者，最终只有一条 session rule（自动去重）。

---

### User Story 5 - Deny 优先级永远高于 Session Allow (Priority: P2)

即使 session 中已经授权了某个操作，如果策略规则中有匹配的 DENY 规则，仍然拒绝执行。安全优先于便利。

**Why this priority**: 安全优先级是权限系统的基石。不能因为重构改变这个行为。

**Independent Test**: 在 Engine 中同时存在一条 session allow（对 `bash` 的某操作）和一条 policy deny（对 `bash` 的危险命令），验证 deny 仍然拒绝。

**Acceptance Scenarios**:

1. **Given** Engine 的策略规则中有 DENY `(bash, "rm -rf /*")`，session 中有一条 ALLOW `(bash, "rm *")`（通过 `replace_session_rules` 恢复），**When** 调用 `evaluate("bash", {"command": "rm -rf /"})`，**Then** 返回 DENY（DENY 规则先于 SESSION ALLOW 匹配，且 DENY 规则不因 session grant 而失效）。
2. **Given** Engine 的策略规则中有 DENY `(bash, "sudo *")`，session 中有 ALLOW `(bash, "git status")`，**When** 调用 `evaluate("bash", {"command": "sudo rm -rf /"})`，**Then** 返回 DENY。

---

### Edge Cases

- **策略规则中存在重复自然键 `(tool_name, rule_content)`**: 在 Engine 初始化时构建 `_policy_rules_by_key` 索引时，发现重复 key 应立即抛出 `InvalidPermissionRule`，阻止启动。
- **`allow_for_session()` 传入非 ASK 的 EvalResult**: Engine 应抛出 `ValueError`（这是调用方 bug，非可恢复的业务异常）。
- **`allow_for_session()` 传入 `matched_rule=None` 的 EvalResult**: 抛出 `NonPersistablePermission`——fallback ASK 没有稳定的策略规则作为授权依据。
- **`allow_for_session()` 传入的 `matched_rule` 不属于当前 Engine**: 如果 `matched_rule.rule_id` 在 `_policy_rules_by_id` 中找不到，或找到的不是同一个对象，抛出 `InvalidPermissionRule`。
- **会话规则与策略规则自然键矛盾**: 如果 session 中写入了一条 `(bash, "rm *")` 的 ALLOW，但该 key 对应的策略规则是 DENY（而非 ASK），说明策略规则配置有问题——应在初始化时校验，不允许 ASK 和 DENY 共享自然键。
- **listener 抛异常**: Engine 调用 listener 前不安装运行时规则，listener 异常向上传播——不会留下"内存已授权但数据库未记录"的不一致状态。
- **并发**: 本项目是单线程 CLI，不处理并发。如果未来引入异步，需重新评估。
- **`approver` 返回未知 decision**: ToolExecutor 按 deny 处理。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `ToolExecutor.__init__()` 必须接受两个必选参数: `permission_engine: PermissionEngine` 和 `approver: Approver`。构造后 engine 和 approver 保存在实例中，不暴露给外部读取。
- **FR-002**: `ToolExecutor.execute()` 必须按固定顺序执行: (1) 工具查找 → (2) `_authorize_tool_call()`（权限评估 + 审批） → (3) 全局 `PreToolUse` Hook（仅非权限扩展） → (4) 工具执行 → (5) 全局 `PostToolUse` Hook。权限在任意节点失败（deny / approver deny / 异常）即返回 error dict。
- **FR-003**: `ToolExecutor._authorize_tool_call()` 私有方法必须封装完整的权限交互流程: `engine.evaluate()` → DENY 直接拒绝 → ALLOW 放行 → ASK 调 approver。approver 返回 "session" 时调 `engine.allow_for_session(result)`；若抛出 `NonPersistablePermission`（fallback ASK），捕获后降级为单次 allow（工具继续执行，不创建 grant）；其他异常（如 approver deny）返回 error dict。
- **FR-004**: `PermissionEngine` 必须提供 `set_grant_listener(listener: Callable[[PermissionGrant], None] | None) -> None`。
- **FR-005**: `PermissionEngine.allow_for_session(result: EvalResult) -> PermissionGrant` 必须: (a) 校验 `result.behavior == ASK`；(b) 校验 `result.matched_rule` 非 None 且有 `rule_id`；(c) 校验 `matched_rule` 属于当前 engine；(d) 构造 `PermissionGrant` 和候选 `PermissionRule`；(e) 如果 listener 存在，先持久化后安装；(f) 返回 grant。持久化失败（listener 抛异常）时，不安装内存规则，异常向上传播。
- **FR-006**: `PermissionEngine.replace_session_rules(grants: list[PermissionGrant]) -> None` 必须: (a) 对每个 grant 调用 `_build_session_rule()` 构建候选；(b) 任一 grant 无效则抛出 `InvalidPermissionGrant`，保留原 `_session_rules`；(c) 全部成功后才原子替换 `_session_rules`。此方法不触发 listener。
- **FR-007**: `PermissionEngine.evaluate()` 管线必须保持: Gate 1 DENY → Gate 2 ALLOW（session + policy） → Gate 3 ASK（排除已被 session grant 覆盖的 key） → fallback（default_behavior）。
- **FR-008**: DENY 规则必须始终保留在管线中，不受 session grants 影响。SESSION ALLOW 只覆盖同 `(tool_name, rule_content)` 的 POLICY ASK。
- **FR-009**: Engine 初始化时必须构建 `_policy_rules_by_key: dict[tuple[str, str], PermissionRule]` 索引。如果发现重复 key，立即抛出 `InvalidPermissionRule`。同时维护 `_policy_rules_by_id: dict[str, PermissionRule]` 用于 `allow_for_session` 的规则归属验证。
- **FR-010**: `_session_rules` 内部类型改为 `dict[tuple[str, str], PermissionRule]`，key 为 `(tool_name, rule_content)`。删除 `_session_counter`。
- **FR-011**: `EvalResult.rule` 重命名为 `EvalResult.matched_rule`，语义明确为: 非 None = 命中了具体策略规则，None = 使用了 default_behavior fallback。
- **FR-012**: `PermissionGrant` 必须为 `@dataclass(frozen=True)`，字段: `tool_name: str`，`rule_content: str`。
- **FR-013**: 删除以下公开 API: `revoke_session_rule()`（PermissionEngine），`clear_session_rules()`（PermissionEngine），`build_tool_executor()`（executor 模块），`create_permission_hook()`（permission 模块）。
- **FR-014**: 保留 `create_engine(project_root, default_behavior)` 工厂函数，职责不变。
- **FR-015**: `PermissionRule.rule_id` 保留但仅用于 Engine 内部诊断日志/错误消息，不作为公开契约进入 `PermissionGrant` 或持久化。
- **FR-016**: 新异常类型必须定义在 `tooling/permission/exceptions.py`: `PermissionArchitectureError`（基类）、`NonPersistablePermission`、`InvalidPermissionGrant`、`InvalidPermissionRule`。从 `tooling.permission` 公共入口导出。
- **FR-017**: 全局 `HOOKS["PreToolUse"]` 中不得注册任何 PermissionEngine callback。`trigger_hooks("PreToolUse")` 仍保留在 executor 执行管线中，仅承载日志/观测等非权限扩展。
- **FR-018**: `tooling/__init__.py` 必须更新导出列表，移除 `build_tool_executor`，新增 `PermissionGrant` 和异常类型。

### Key Entities

- **PermissionGrant**: 不可变数据类 `(tool_name, rule_content)`。代表一条用户授权的、可持久化的权限事实。不含 condition 闭包、不含 rule_id。这是 PermissionEngine 与 SessionController／Repository 之间的唯一数据契约。
- **EvalResult**: 权限评估结果 `(behavior, reason, matched_rule)`。`matched_rule=None` 表示 fallback；`matched_rule` 非 None 表示命中了具体策略规则（可产生 grant）。
- **PermissionRule**: 运行时规则对象，包含 `condition` 闭包、`rule_behavior`、`rule_id`（内部诊断用）。策略规则在 Engine 初始化时一次性构建；session 规则由 Engine 从 `PermissionGrant` 重建（复用原策略规则的 condition）。
- **Approver**: `Callable[[str, dict, str | None], dict]` —— 审批回调，负责与用户交互。返回 `{"decision": "allow"|"deny"|"session", "reason"?: str}`。ToolExecutor 构造参数。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 两个 ToolExecutor 实例，各自绑定不同的 PermissionEngine，在同一进程中运行时相互不影响。具体验证: executor A（deny-all engine）不阻止 executor B（allow-all engine）成功执行工具。
- **SC-002**: `PermissionEngine.allow_for_session()` 成功时，listener 恰好被调用一次，参数 grant 的 `tool_name` 和 `rule_content` 与触发 ASK 的策略规则一致。
- **SC-003**: `PermissionEngine.replace_session_rules()` 不触发 listener，无论 grants 为空或非空。
- **SC-004**: `replace_session_rules()` 在任一 grant 无效时保持原有 `_session_rules` 不变（原子性保证）。
- **SC-005**: DENY 规则在所有场景下优先于 SESSION ALLOW。即使 session grants 中存在对应操作授权，DENY 仍然拒绝。
- **SC-006**: 生产代码路径（main.py）不再通过全局 `register_hook("PreToolUse", ...)` 注册任何 PermissionEngine callback。grep 确认零匹配。
- **SC-007**: 所有现有测试通过 + 新增权限模块测试覆盖 grant/listener、replace、原子性、双 executor 隔离。
- **SC-008**: fallback ASK（`matched_rule=None`）选择 "session" 时，Engine 拒绝创建 grant（`NonPersistablePermission`），操作仅当次允许。

## Assumptions

- 本项目是单线程 CLI Python 应用，无并发考虑。
- SessionController 功能由后续迭代（specs/005-session-persistence）实现，本次只提供其所需的 Permission 侧接口。
- 现有的 19 条内置策略规则中，可产生持久授权的 ASK 规则的 `(tool_name, rule_content)` 目前已经是唯一的，不需要调整策略内容本身。
- `create_engine()` 作为 PermissionEngine 的唯一工厂保留，SessionController 不使用独立的 engine 构造路径。
- `terminal_approver` 保持现有交互方式（终端 input），本次不修改审批 UI。不在 approver 中根据 `matched_rule` 是否为 None 来隐藏"始终允许"选项——这属于后续 approver 契约调整。
- ToolExecutor 执行管线中，权限异常（包括 approver 返回未知 decision）均按 deny 处理，返回 error dict 而非抛异常。
- `agent.py`、`conversation.py` 的 Agent 循环逻辑本次不修改。SubAgent 共享 executor 的行为不变。
