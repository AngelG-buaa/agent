# Research: Permission 系统实例级重构

**Date**: 2026-07-13
**Status**: Complete — 所有技术决策已通过 spec 阶段 19 轮采访确认

## 1. 架构模式：全局 Hook → 实例级注入

**Decision**: ToolExecutor 构造时接收 `permission_engine` 和 `approver`，权限检查在 executor 内部执行，不再注册到全局 `HOOKS["PreToolUse"]`。

**Rationale**:
- 当前全局 Hook 注册导致同一进程多个 executor 的权限 callback 互相干扰（架构文档 Section 1 根因分析）
- 权限门禁是 executor 的固定组成部分，不是可选横切扩展（架构文档 Section 1）
- 构造注入（composition root）是 Python CLI 项目的主流模式，与现有 `Agent(llm, executor)` 风格一致

**Alternatives considered**:
- **保持全局 Hook + executor ID 过滤**: 需要 callback 内部判断 executor 身份，增加复杂度且不解决根本问题（全局状态仍存在）
- **实例级 Hook 系统（每个 executor 有自己的 PreToolUse 列表）**: 过度设计。当前只有日志/观测使用非权限 PreToolUse，暂时不需要（架构文档 Section 3 非目标）

## 2. 数据契约：PermissionGrant

**Decision**: `PermissionGrant(tool_name, rule_content)` 作为 PermissionEngine ↔ SessionController 的唯一数据契约。`(tool_name, rule_content)` 是稳定自然键。

**Rationale**:
- 不含 `condition` 闭包（不可序列化）和 `rule_id`（临时标识），使 Controller/Repository 层完全不了解 PermissionRule 内部结构
- 自然键提供稳定的去重和查找能力——Engine 根据它从 `_policy_rules_by_key` 索引中找到原策略规则并复用 condition
- 与 `PermissionRule` 的 `(tool_name, rule_content)` 自然键一一对应

**Alternatives considered**:
- **直接暴露 `PermissionRule`**: 泄露 condition 闭包和 RuleBehavior，Controller 需要理解内部运行时类型
- **增加 `source_rule_id` 字段**: 形成重复事实（`rule_id` + `(tool_name, rule_content)` 定位同一规则），已在采访 Q12 中否决

## 3. Session Rules 状态模型

**Decision**: `_session_rules: dict[tuple[str, str], PermissionRule]`，仅通过 `allow_for_session()` 和 `replace_session_rules()` 两个入口修改。

**Rationale**:
- 全量替换是唯一修改路径——消除 `revoke_session_rule()` 和 `clear_session_rules()` 的多路径状态管理风险
- 原子替换：先构建候选集合，全部成功后替换现有 `_session_rules`（采访 Q14）
- 自然键自动去重：同一 `(tool_name, rule_content)` 再次授权只覆盖原规则

**Alternatives considered**:
- **增量 CRUD（allow/revoke/clear 三个入口）**: 需要同步持久化层，容易产生不一致（采访 Q5）
- **保留 `clear_session_rules()`**: 与 `replace_session_rules([])` 语义完全等价，多余接口（采访 Q4）

## 4. Grant Listener 语义

**Decision**: `set_grant_listener(listener)` — listener 仅在用户交互产生新 grant 时触发（持久化），恢复/切换/清空不触发。

**Rationale**:
- 职责分离：Engine 负责权限评估和内存状态；listener 负责持久化通知；Controller 负责编排
- 持久化失败时 Engine 不安装内存规则——保证"先持久化后改变内存"的不变性（采访 Q4）
- `replace_session_rules()` 不需要 `notify` 参数——从类型层面消除误用

**Alternatives considered**:
- **每次 session rule 变化都通知**: 恢复历史 grants 时会重复持久化已存在的数据
- **提供 `notify=False` 参数**: 调用者可能错误传入 `notify=True`，接口不够安全（采访 Q4）

## 5. 管线优先级模型

**Decision**: 显式覆盖替代"先入为主"的隐式去重。

```text
DENY: 始终保留（安全优先）
SESSION ALLOW: 覆盖同 natural_key 的 POLICY ASK
POLICY ALLOW: 正常保留
POLICY ASK: 排除已被 session grant 覆盖的 key
```

评估顺序: deny → allow (session + policy) → ask (uncovered) → fallback

**Rationale**:
- DENY 永远优先于 ALLOW（安全基础）
- Session grant 覆盖对应 ASK 规则是精确语义，不是"先入为主"的副作用
- 在 Engine 初始化时校验 `(tool_name, rule_content)` 唯一性——防止 DENY 和 ASK 共享自然键

**Alternatives considered**:
- **先入为主去重**: 语义隐晦，依赖列表追加顺序而非业务规则（采访 Q18）

## 6. Approver 归属

**Decision**: `approver` 是 ToolExecutor 的构造参数（非 PermissionEngine）。

**Rationale**:
- approver 是交互层关注点，与 Engine 的纯评估职责不同
- 不同 executor 可能需要不同 approver（如 terminal vs CI）
- `_authorize_tool_call()` 编排 evaluate → approver → engine.allow_for_session 流程，职责内聚在 ToolExecutor

**Alternatives considered**:
- **approver 跟随 Engine**: 混淆评估和交互，Engine 变成非纯函数（采访 Q2）
