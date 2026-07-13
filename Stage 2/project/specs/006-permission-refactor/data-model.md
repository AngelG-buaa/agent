# Data Model: Permission 系统实例级重构

**Date**: 2026-07-13
**Feature**: [spec.md](./spec.md)

## Entity Relationship

```text
PermissionEngine
  ├── _policy_rules: list[PermissionRule]           # 策略规则（构造时一次性构建，不变）
  ├── _policy_rules_by_key: dict[GrantKey, PermissionRule]  # (tool_name, rule_content) → rule
  ├── _policy_rules_by_id: dict[str, PermissionRule]        # rule_id → rule (诊断用)
  ├── _session_rules: dict[GrantKey, PermissionRule] # 会话规则（运行时可变）
  ├── _grant_listener: Callable[[PermissionGrant], None] | None
  └── default_behavior: str

ToolExecutor
  ├── _registry: ToolRegistry
  ├── _permission_engine: PermissionEngine  # 构造注入，不暴露
  └── _approver: Approver                   # 构造注入，不暴露
```

## Entities

### PermissionGrant (NEW — 公开数据契约)

```text
@dataclass(frozen=True)
class PermissionGrant:
    tool_name: str       # 工具名（如 "bash", "write_file"）
    rule_content: str    # 规则内容摘要（如 "rm *", "*"）

Natural Key: (tool_name, rule_content)
```

**Invariants**:
- `(tool_name, rule_content)` 必须唯一对应一条策略 ASK 规则
- 不包含 condition 闭包、rule_id、RuleBehavior
- 可安全序列化/反序列化（供 SQLite/JSON 持久化）

**Lifecycle**:
1. 创建：用户通过 Approver 选择 "session" → Engine 从 `EvalResult.matched_rule` 构造
2. 持久化：listener callback 将 grant 写入 Repository
3. 恢复：Controller 从 Repository 加载 grants → `engine.replace_session_rules(grants)`
4. 删除：`replace_session_rules(updated_grants)` — 不在 list 中的 grant 自动移除

### EvalResult (MODIFIED)

```text
@dataclass
class EvalResult:
    behavior: RuleBehavior             # DENY | ALLOW | ASK
    reason: str | None = None          # 命中原因（用户可读）
    matched_rule: PermissionRule | None = None  # 命中的策略规则（None = fallback）

Renamed: rule → matched_rule
```

**Semantics**:
- `matched_rule is not None` → 命中了具体策略规则，可用于产生 PermissionGrant
- `matched_rule is None` → 使用 default_behavior 作为 fallback，不能产生持久 grant

### PermissionRule (UNCHANGED, internal)

```text
@dataclass
class PermissionRule:
    tool_name: str
    rule_behavior: RuleBehavior  # DENY | ALLOW | ASK
    rule_content: str            # 规则摘要
    message: str                 # 给用户的说明文字
    condition: Callable[[str, dict], bool]  # 匹配函数
    rule_id: str | None = None   # 诊断用（不进入持久化契约）
```

**Invariants** (NEW — Engine 初始化时校验):
- 策略规则集合中 `(tool_name, rule_content)` 必须唯一
- 可产生持久授权的 ASK 规则必须有 `rule_id`
- 同一 `(tool_name, rule_content)` 不能同时有 DENY 和 ASK 规则

### Approver (UNCHANGED signature)

```text
Approver = Callable[[str, dict, str | None], dict]
# (tool_name, params, reason) -> {"decision": "allow"|"deny"|"session", "reason"?: str}
```

### ToolExecutor (MODIFIED constructor)

```text
class ToolExecutor:
    __init__(self, permission_engine: PermissionEngine, approver: Approver)
    # permission_engine 和 approver 存储在私有字段，不暴露给外部读取
```

## State Transitions

### Session Rules

```text
空状态
  │
  ├─→ allow_for_session(result) ──→ 新增一条 rule（触发 listener）
  │
  ├─→ replace_session_rules([g1, g2]) ──→ 替换为 {g1, g2}（不触发 listener）
  │
  └─→ replace_session_rules([]) ──→ 清空（不触发 listener）
```

### Grant Creation Flow

```text
evaluate() → ASK + matched_rule 非空
  → approver 返回 "session"
    → engine.allow_for_session(result)
      ├─ valid: create grant → listener(grant) success → install rule → return grant
      ├─ listener fails: exception propagates (no rule installed)
      └─ invalid (fallback): raise NonPersistablePermission → ToolExecutor degrades to single allow
```

## Type Aliases

```text
GrantKey = tuple[str, str]  # (tool_name, rule_content)
```
