# Research: Session 持久化（架构重修版）

**Feature**: 007-session-persistence
**Date**: 2026-07-14
**Status**: Complete —— 所有技术决策已在 architecture-design.md 中明确，本文件做结构化记录

## 1. 架构模式选择

### Decision: Conversation → SessionController → SessionManager 三层结构

### Rationale

旧架构（Hook 驱动）将 session 生命周期伪装为横切关注点，实际产生了 5 种隐式耦合：

```
sid_ref 闭包 + 全局 HOOKS + 私有字段访问 + 模块级 Todo + main.py 状态分支
```

这属于语法解耦、语义耦合。SessionController 将分散在 5 个位置的 session 状态集中到一个显式概念中。

### Alternatives Considered

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **继续堆叠 Hook** | 不新增概念 | 缺少 session 作用域，无法表达原子切换 | ❌ 拒绝 |
| **大型贫血 Service** | 集中管理 | 容易变成 God Object，把 main.py 的职责迁移 | ❌ 拒绝 |
| **事件溯源** | 完整审计 | 对单用户 CLI 过度设计 | ❌ 拒绝 |
| **三层结构（采用）** | 显式生命周期、每层可独立测试、符合 Constitution X | 新增一个概念（Controller） | ✅ 采用 |

### References

- [architecture-design.md §4](../005-session-persistence/architecture-design.md#4-根因与设计决策)
- Constitution X: Recognize When to Elevate Design

## 2. 消息持久化协议

### Decision: 先写 SQLite（事务），成功后再追加到 working context

### Rationale

消息进入 working context 的入口只有一个（`SessionController.append_message()`），提交顺序不可交换：

```
1. Repository 开启事务
2. 计算并写入 seq
3. 更新 updated_at、message_count、title（如需要）
4. COMMIT
5. 将消息追加到 working context (ActiveSession.messages)
```

如果步骤 1-4 失败，内存 messages 不增加该消息。这保证了一致性（不会出现"内存有但数据库无"的状态）。

working context 和 durable transcript 是有意分离的两个视图：
- working context 被 compact 管线原地修改
- durable transcript 保存原始完整消息
- 恢复时加载原始 transcript，compact 按需重新压缩

### Alternatives Considered

| 方案 | 风险 | 结论 |
|------|------|------|
| 先写内存再写库 | 写库失败后内存已污染 | ❌ 拒绝 |
| 每轮 loop 后批量写 | 与 compact 存在竞态：compact 修改内存后写入的是"被截断的"消息 | ❌ 拒绝 |
| compact 后写原始消息 | 需要缓存原始消息，增加复杂度 | ❌ 拒绝 |

### References

- [architecture-design.md §7](../005-session-persistence/architecture-design.md#7-消息提交协议)
- Spec FR-002

## 3. 消息出口机制

### Decision: Agent.run() 新增 `on_message: Callable[[dict], None] | None = None` 参数

### Rationale

替代旧的 `MessageAppended` 全局 Hook。关键差异：

- **Hook（旧）**：全局注册，无 session 作用域，Agent 不知谁在监听
- **回调（新）**：主 Agent 由 SessionController 注入 session 级回调；SubAgent 不传 → 使用默认 `messages.append`

这同时解决了 SubAgent 消息隔离问题：SubAgent 的 `on_message` 是 `None`，因此其消息只存在于局部 `messages` 列表，不进主 session 数据库。

### 语义

```python
Agent.run(
    messages: list[dict],
    on_message: Callable[[dict], None] | None = None,
) -> str
```

- `on_message is None` → 使用 `messages.append`（SubAgent + 无持久化测试）
- `on_message` 由 SessionController 传入 → 回调内部先写库再 append

### Alternatives Considered

| 方案 | 问题 | 结论 |
|------|------|------|
| 全局 Hook MessageAppended | 无 session 作用域，SubAgent 需手动过滤 | ❌ 拒绝 |
| Agent 内部直接调用 SessionManager | Agent 依赖 Repository → 违反 Constitution IX | ❌ 拒绝 |
| 回调参数（采用） | Agent 零新增依赖，SubAgent 天然隔离 | ✅ 采用 |

### References

- [architecture-design.md §6.6](../005-session-persistence/architecture-design.md#66-agent)
- Constitution IX: Keep the Agent Loop Simple

## 4. SQLite 连接管理

### Decision: 每个公开方法使用 `contextlib.closing(sqlite3.connect(...))`

### Rationale

- 不使用连接缓存或连接池——单用户 CLI 不需要
- `closing` + `with conn:` 统一保证 commit/rollback + close
- 避免 Windows 下文件锁泄漏（旧测试曾出现此问题）

### 关键实现

- 写事务需要 seq 分配的，在 `with conn:` 块开头执行 `BEGIN IMMEDIATE`
- 每次连接启用 `PRAGMA foreign_keys = ON`
- `updated_at` 由 Python 生成带时区的 UTC ISO-8601 时间戳

### References

- [architecture-design.md §9.2](../005-session-persistence/architecture-design.md#92-事务与连接)

## 5. 路径安全

### Decision: UUID 校验 + 路径越界检查双重防护

### Rationale

所有外部 session id 先通过 `uuid.UUID()` 校验格式。构造数据库路径后必须确认 resolved path 位于 `sessions_dir` 下，避免通过外部修改数据库 metadata 造成路径越界。

```python
uuid.UUID(session_id)  # 格式校验
db_path = os.path.realpath(os.path.join(sessions_dir, f"{session_id}.db"))
if not db_path.startswith(os.path.realpath(sessions_dir)):
    raise SessionCorrupted("路径越界")
```

### References

- [architecture-design.md §9.3](../005-session-persistence/architecture-design.md#93-路径安全)
- Spec FR-015

## 6. 权限隔离策略

### Decision: 使用 PermissionEngine 公开接口（`set_grant_listener` + `replace_session_rules`），通过 `PermissionGrant` 值对象传递

### Rationale

006-permission-refactor 已将权限门禁移入 ToolExecutor 实例。Session 持久化复用这些公开接口：

- **新 grant**：`engine.set_grant_listener(controller._on_grant)` → `allow_for_session()` 时自动回调
- **恢复 grant**：`engine.replace_session_rules(grants)` → 原子替换，不触发 listener
- **切换 grant**：先 `replace_session_rules(target_grants)` → 目标为空即清空

`clear_session_rules()` 可以由 `replace_session_rules([])` 替代，无需新增方法。

### Data Contract

```python
@dataclass(frozen=True)
class PermissionGrant:
    tool_name: str
    rule_content: str
```

`(tool_name, rule_content)` 是稳定自然键，精确对应策略中的一条 ASK 规则。

### Alternatives Considered

| 方案 | 问题 | 结论 |
|------|------|------|
| engine._save_callback（旧） | 访问私有字段 | ❌ 拒绝 |
| executor._permission_engine（旧） | 反向读取私有字段 | ❌ 拒绝 |
| 公开接口（采用） | —— | ✅ 采用 |

### References

- [architecture-design.md §10](../005-session-persistence/architecture-design.md#10-权限隔离)
- [006-permission-refactor spec](../../006-permission-refactor/spec.md)
- Spec FR-004, FR-011

## 7. Todo 隔离策略

### Decision: 模块级 Todo + `snapshot_todos()` / `replace_todos()` + `TodoReminderHandle`

### Rationale

CLI 同时只有一个 active session，模块级状态是可以正确隔离的。这是本次范围内的明确妥协，未来支持并发 session 时再引入注入式 TodoState。

关键设计：
- `replace_todos()` 使用 `CURRENT_TODOS.clear()` + `extend()`，不重新绑定列表引用
- `TodoReminderHandle` 暴露 `reset()` 和 `dispose()`，封装 reminder 计数器
- 空列表也必须替换（覆盖旧状态）
- reminder 计数不持久化；恢复后从零开始

### Alternatives Considered

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| 注入式 TodoState | 无全局状态 | 需要传递到 TodoWriteTool 内部，改造范围大 | ❌ 延后 |
| 持久化 reminder 计数 | 完整恢复 | reminder 是临时 UI 状态，不应进持久化链 | ❌ 拒绝 |
| 模块级 + handle（采用） | 改动最小，满足当前需求 | 未来并发需重构 | ✅ 采用 |

### References

- [architecture-design.md §11](../005-session-persistence/architecture-design.md#11-todo-隔离)
- Spec FR-003, FR-017

## 8. 草稿数据库兼容

### Decision: 不实现旧草稿 schema 自动迁移

### Rationale

该 feature 尚未提交到主分支，旧实现生成的数据库属于草稿状态。缺少表或字段时让 SQL 查询自然失败，由用户删除旧测试数据库后重新创建，不增加迁移和版本判断。

### References

- [architecture-design.md §9.4](../005-session-persistence/architecture-design.md#94-草稿数据库兼容)

## 9. 消息归一化统一入口

### Decision: 删除 `Agent._normalize_message()`，统一使用 `agent.utils.normalize_message(msg) → dict`

### Rationale

初步实现中消息归一化在 Agent 私有方法中完成，但 `filter_assistant_message()` 在 utils 中做类似转换——同一逻辑存在两份。归一化是"跨模块复用、无状态且无业务所有权"的纯函数，应该放在 `utils.py`。

合并后的 `normalize_message()` 将 SDK message 转换为仅包含 `role`、`content`、`tool_calls`、`tool_call_id` 四个字段的 dict，同时承担 `filter_assistant_message()` 的过滤职责。

### Alternatives Considered

| 方案 | 问题 | 结论 |
|------|------|------|
| 保留在 Agent 中 | 私有方法，其他模块无法复用 | ❌ 拒绝 |
| 放在 SessionManager 中 | Repository 不应关心消息格式转换 | ❌ 拒绝 |
| utils.py 公共函数（采用） | 零依赖，单一入口，符合 Constitution III | ✅ 采用 |

### References

- [architecture-refactor-plan.md §3.3](./architecture-refactor-plan.md#33-agentutilspy共享消息与输出工具)

## 10. Hook 注册返回 Disposer

### Decision: `register_hook(event, callback) → Callable[[], None]`

### Rationale

初步实现中 `TodoReminderHandle.dispose()` 直接读写全局 `HOOKS` 字典，这违反了封装原则——Handle 不应该知道 Hook 系统的内部存储结构。改为让 `register_hook()` 返回一个幂等 disposer 函数，Handle 只保存并调用这些 disposer。

```python
disposer = register_hook("PreLLMCall", on_pre_llm_call)
# disposer() → 从 HOOKS 中移除该回调，幂等
```

### Benefits

- `TodoReminderHandle` 不再导入或访问 `HOOKS`
- `register_hook()` 的返回值和 `trigger_hooks()` 的查询是对称的——注册方持有清理 token
- disposer 幂等（重复调用无害），简化 Controller.close() 的 try/finally 逻辑

### References

- [architecture-refactor-plan.md §3.7](./architecture-refactor-plan.md#37-hookspy-与-toolstodo_writepy)

## 11. SessionController 文件独立

### Decision: 将 SessionController、ActiveSession、ActiveSessionDeletionError 移入 `agent/session_controller.py`

### Rationale

初步实现中 SessionController 和 Conversation 同文件。按职责拆分：
- SessionController：active session 生命周期 + 消息出口 + grant listener + Todo Hook + disposer
- Conversation：终端适配器，只做 REPL I/O + 命令解析 + 调用 Controller

两者的变化原因不同——Controller 变化源于 session 生命周期需求，Conversation 变化源于交互方式变化。按变化原因拆分。

### Module Ownership

| 元素 | 归属 |
|------|------|
| SessionController | `agent/session_controller.py` |
| ActiveSession | `agent/session_controller.py` |
| ActiveSessionDeletionError | `agent/session_controller.py` |
| Conversation | `agent/conversation.py` |

SessionManager 中移除 ActiveSession 和 ActiveSessionDeletionError（它们属于应用层，非 Repository）。

### References

- [architecture-refactor-plan.md §3.4-3.5](./architecture-refactor-plan.md#34-agentsession_controllerpy应用层-sessioncontroller)

## 12. 新 Session 保存真实 System Prompt

### Decision: `SessionManager.create_session(system_message)` 在创建事务中保存调用者传入的真实 system prompt

### Rationale

初步实现中 Controller 传入空占位符 `{"role": "system", "content": ""}`，在首轮对话时再注入真实 prompt。这导致数据库中 system message 为空字符串——如果用户在注入前退出，恢复后 system prompt 丢失。

修正：Controller/Main 在创建 session 时直接传入 Agent 的真实 system prompt，Repository 在创建事务中保存。

### References

- [architecture-refactor-plan.md §2.1](./architecture-refactor-plan.md#21-消息持久化)
- Spec FR-001

## 13. Conversation.start(resume: bool) 统一接口

### Decision: `Conversation.start(resume: bool = False) → None` 替代 `start(initial_session_id=None)`

### Rationale

初步实现有两个分散的启动路径：`main.py::_list_and_act()` 处理 `--resume`，`Conversation.start()` 处理新建。重构后：

- `start(resume=False)`：创建新 session，进入 REPL
- `start(resume=True)`：显示 session 列表，用户选择后恢复；无历史或取消则创建新 session
- 启动选择期间不存在 active session
- 启动菜单和 REPL 菜单使用同一套循环式流程，不使用递归刷新

`main.py` 变为纯装配点：`conv.start(resume=args.resume)`。

### References

- [architecture-refactor-plan.md §5.4](./architecture-refactor-plan.md#54-conversation)
- [architecture-refactor-plan.md §7.1-7.2](./architecture-refactor-plan.md#71-普通启动)
