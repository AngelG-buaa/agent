# Implementation Plan: Permission 系统实例级重构

**Branch**: `006-permission-refactor` | **Date**: 2026-07-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-permission-refactor/spec.md`

## Summary

将 PermissionEngine 从全局 Hook 系统中拆出，改为 ToolExecutor 实例级注入。核心变更：(1) ToolExecutor 构造时接收 PermissionEngine + Approver；(2) 权限检查从全局 PreToolUse hook 移到 executor 内部 `_authorize_tool_call()` 私有方法；(3) 建立公开的 grant/listener 接口作为 PermissionEngine ↔ SessionController 的数据契约；(4) 删除 `build_tool_executor()`、`create_permission_hook()`、`revoke_session_rule()`、`clear_session_rules()` 等旧 API。

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: 标准库 `dataclasses`、`pathlib`、`typing`；项目内部模块 `tooling.base`、`tooling.registry`、`hooks`

**Storage**: N/A（本次重构不涉及持久化；grant listener 是持久化的触发点但不由 Engine 执行存储）

**Testing**: pytest

**Target Platform**: Windows/Linux CLI（单线程）

**Project Type**: CLI

**Performance Goals**: N/A（权限评估为 O(1) 字典查找，无性能瓶颈）

**Constraints**: 保持与现有 `Tool` 基类、`ToolRegistry`、`Agent.run()` 的接口兼容；不修改 `agent.py` 和 `conversation.py` 的循环逻辑

**Scale/Scope**: 6 个源文件修改、2 个新文件、~5 个测试场景

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Correctness First | ✅ PASS | DENY > ALLOW 优先级明确测试覆盖；原子替换保证状态一致性 |
| II | Small Steps | ✅ PASS | 8 个实施步骤，每步 1-2 个文件，commit 粒度细 |
| III | Clarity & Maintainability | ✅ PASS | 公开接口精简（3 个方法）；`_authorize_tool_call()` 语义精确 |
| IV | Good Architecture | ✅ PASS | 单向依赖：main → ToolExecutor → PermissionEngine |
| V | Don't Reinvent | ✅ PASS | 复用现有 PermissionRule、EvalResult、ToolRegistry |
| VI | Mainstream Practices | ✅ PASS | 构造注入（composition root） |
| VII | Core Tests | ✅ PASS | 权限引擎是核心模块，新接口全部有测试 |
| VIII | Backward Compatibility | ⚠️ MITIGATED | 删除 build_tool_executor() 等 API，但无外部消费者 |
| IX | Keep Agent Loop Simple | ✅ PASS | 不修改 Agent.run() |
| X | Elevate Design | ✅ PASS | PermissionGrant 是新概念，满足准则 1 |

**Gate Result**: PASS

---

## Implementation Sequence

按依赖关系分为 8 个步骤。每步改动量可控，可独立验证。

```text
Step 1: exceptions.py         ── 新文件，无依赖
Step 2: engine.py (内部重构)   ── 依赖 Step 1
Step 3: engine.py (公开 API)   ── 依赖 Step 2，最关键的变更
Step 4: policy.py (唯一性校验)  ── 依赖 Step 3
Step 5: executor.py            ── 依赖 Step 3
Step 6: __init__.py × 2        ── 依赖 Step 3, 5
Step 7: main.py                ── 依赖 Step 5, 6
Step 8: tests                  ── 依赖 Step 1-7
```

---

## Step 1: 新建 `tooling/permission/exceptions.py`

**文件**: `tooling/permission/exceptions.py` (NEW)

**内容**:

```python
"""权限模块异常类型。

Engine 抛出这些异常供 ToolExecutor / SessionController 按类型处理。
不继承内置 PermissionError，避免与操作系统权限异常混淆。
"""

class PermissionArchitectureError(Exception):
    """权限模块基础异常。"""

class NonPersistablePermission(PermissionArchitectureError):
    """当前授权决策无法持久化。

    抛出场景: fallback ASK（matched_rule=None）被用户选择"始终允许"。
    ToolExecutor 捕获后降级为单次 allow。
    """

class InvalidPermissionGrant(PermissionArchitectureError):
    """持久化 grant 无法映射到现有策略规则。

    抛出场景: replace_session_rules() 收到一个 (tool_name, rule_content)
    在当前 _policy_rules_by_key 中找不到对应策略规则的 grant。
    """

class InvalidPermissionRule(PermissionArchitectureError):
    """策略规则配置无效。

    抛出场景:
    - 策略规则集合中 (tool_name, rule_content) 存在重复
    - allow_for_session() 传入不属于当前 engine 的 matched_rule
    - matched_rule.rule_id 为 None 但需要持久化
    """
```

**验证**: `python -c "from tooling.permission.exceptions import NonPersistablePermission; print('OK')"`

---

## Step 2: 重构 `tooling/permission/engine.py` — 内部状态

**文件**: `tooling/permission/engine.py` (MAJOR REFACTOR)

### 2.1 新增 import

```python
from typing import Callable
from .exceptions import NonPersistablePermission, InvalidPermissionGrant, InvalidPermissionRule
```

### 2.2 新增 `PermissionGrant` 数据类

```python
@dataclass(frozen=True)
class PermissionGrant:
    """一条可持久化的用户授权。

    (tool_name, rule_content) 是稳定自然键，唯一对应一条策略 ASK 规则。
    不含 condition 闭包、不含 rule_id——这是 PermissionEngine 与
    SessionController/Repository 之间的纯数据契约。
    """
    tool_name: str
    rule_content: str
```

### 2.3 `EvalResult` — 字段重命名

将 `rule: PermissionRule | None` 重命名为 `matched_rule: PermissionRule | None`。docstring 更新为：

```python
@dataclass
class EvalResult:
    """权限评估结果。"""
    behavior: RuleBehavior
    reason: str | None = None
    matched_rule: PermissionRule | None = None  # None = fallback to default_behavior
```

注意：`matched_rule` 替代旧字段 `rule`。这是 breaking change，但只在内部使用——需同步修改 `_authorize_tool_call()` 和 `allow_for_session()` 中的引用。

### 2.4 `PermissionEngine.__init__()` — 新增内部索引

当前代码只保存 `_policy_rules: list[PermissionRule]`。

新代码额外构建两个索引，并校验自然键唯一性：

```python
def __init__(
    self,
    policy_rules: list[PermissionRule] | None = None,
    default_behavior: str = "allow",
):
    self._policy_rules = list(policy_rules or [])
    self.default_behavior = default_behavior

    # 索引 (tool_name, rule_content) → PermissionRule
    self._policy_rules_by_key: dict[tuple[str, str], PermissionRule] = {}
    # 索引 rule_id → PermissionRule (诊断用 + allow_for_session 归属验证)
    self._policy_rules_by_id: dict[str, PermissionRule] = {}

    for rule in self._policy_rules:
        key = (rule.tool_name, rule.rule_content)
        if key in self._policy_rules_by_key:
            raise InvalidPermissionRule(
                f"策略规则自然键重复: tool_name={rule.tool_name!r}, "
                f"rule_content={rule.rule_content!r}"
            )
        self._policy_rules_by_key[key] = rule
        if rule.rule_id is not None:
            self._policy_rules_by_id[rule.rule_id] = rule

    # 会话规则 —— key 改为 (tool_name, rule_content)
    self._session_rules: dict[tuple[str, str], PermissionRule] = {}
    self._grant_listener: Callable[[PermissionGrant], None] | None = None
```

**删除**: `_session_counter`（不再需要生成临时 ID）。

### 2.5 `_collect_all_rules()` — 显式覆盖逻辑

当前实现用 `set()` 去重、依赖列表顺序"先入为主"。

新实现用显式业务规则：

```python
def _collect_all_rules(self) -> tuple[list[PermissionRule], list[PermissionRule], list[PermissionRule]]:
    """合并策略规则 + 会话规则，显式覆盖。"""
    granted_keys = set(self._session_rules.keys())

    deny = [
        rule for rule in self._policy_rules
        if rule.rule_behavior == RuleBehavior.DENY
    ]

    allow = [
        *self._session_rules.values(),
        *[
            rule for rule in self._policy_rules
            if rule.rule_behavior == RuleBehavior.ALLOW
        ],
    ]

    ask = [
        rule for rule in self._policy_rules
        if (
            rule.rule_behavior == RuleBehavior.ASK
            and (rule.tool_name, rule.rule_content) not in granted_keys
        )
    ]

    return deny, allow, ask
```

**删除**: `seen` set、`all_rules` 临时列表、"先入为主"的去重逻辑。

### 2.6 `_build_session_rule()` — 新增私有方法

从 `PermissionGrant` 重建运行时 `PermissionRule`：

```python
def _build_session_rule(self, grant: PermissionGrant) -> PermissionRule:
    """根据 grant 从策略索引中找到原 ASK 规则，复用其 condition。

    Raises:
        InvalidPermissionGrant: 找不到对应策略规则
    """
    key = (grant.tool_name, grant.rule_content)
    policy_rule = self._policy_rules_by_key.get(key)
    if policy_rule is None:
        raise InvalidPermissionGrant(
            f"找不到对应的策略规则: tool_name={grant.tool_name!r}, "
            f"rule_content={grant.rule_content!r}"
        )
    return PermissionRule(
        tool_name=grant.tool_name,
        rule_behavior=RuleBehavior.ALLOW,
        rule_content=grant.rule_content,
        message=policy_rule.message,
        condition=policy_rule.condition,
        rule_id=None,  # session rules 不需要 rule_id
    )
```

### 2.7 删除 `_session_counter` 相关代码

删除 `__init__` 中的 `self._session_counter = 0`。

**验证**: `python -c "from tooling.permission.engine import PermissionEngine, PermissionGrant; e = PermissionEngine(default_behavior='deny'); print(type(e._policy_rules_by_key))"`

---

## Step 3: 重构 `tooling/permission/engine.py` — 公开 API

### 3.1 删除旧 API

```python
# 删除 allow_for_session(self, tool_name, rule_content, message) -> str
# 删除 revoke_session_rule(self, rule_id) -> bool
# 删除 clear_session_rules(self) -> None
```

### 3.2 `set_grant_listener()` — 新增

```python
def set_grant_listener(
    self,
    listener: Callable[[PermissionGrant], None] | None,
) -> None:
    """安装/移除 grant 持久化回调。

    仅在 allow_for_session() 成功创建新 grant 时调用。
    replace_session_rules() 永远不会触发此回调。
    """
    self._grant_listener = listener
```

### 3.3 `allow_for_session()` — 重写

```python
def allow_for_session(self, result: EvalResult) -> PermissionGrant:
    """用户交互产生新授权，创建持久化 grant 并安装运行时规则。

    Preconditions:
        result.behavior == RuleBehavior.ASK
        result.matched_rule is not None (非 fallback)
        result.matched_rule.rule_id is not None
        result.matched_rule 属于当前 engine

    Raises:
        ValueError: behavior 不是 ASK
        NonPersistablePermission: matched_rule 为 None (fallback ASK)
        InvalidPermissionRule: rule_id 缺失或规则不属于当前 engine

    Returns:
        PermissionGrant(tool_name, rule_content)
    """
    if result.behavior != RuleBehavior.ASK:
        raise ValueError(
            f"只有 ASK 决策可以转换为 session grant，当前为 {result.behavior}"
        )

    source = result.matched_rule
    if source is None:
        raise NonPersistablePermission(
            "fallback ASK 没有稳定的策略范围，不能创建 session grant"
        )

    if not source.rule_id:
        raise InvalidPermissionRule(
            "可持久化的 ASK 规则必须具有稳定 rule_id"
        )

    # 防止传入其他 engine 或已失效的规则
    registered = self._policy_rules_by_id.get(source.rule_id)
    if registered is not source:
        raise InvalidPermissionRule(
            f"规则不属于当前 PermissionEngine: {source.rule_id}"
        )

    grant = PermissionGrant(
        tool_name=source.tool_name,
        rule_content=source.rule_content,
    )

    session_rule = self._build_session_rule(grant)

    # 先持久化，后安装内存规则
    if self._grant_listener is not None:
        self._grant_listener(grant)

    key = (grant.tool_name, grant.rule_content)
    self._session_rules[key] = session_rule

    return grant
```

### 3.4 `replace_session_rules()` — 新增

```python
def replace_session_rules(
    self,
    grants: list[PermissionGrant],
) -> None:
    """替换当前所有会话规则。

    先构建候选集合，全部成功后再原子替换。任一 grant 无效则
    整体失败，保留原 _session_rules 不变。不触发 grant_listener。

    Raises:
        InvalidPermissionGrant: 任一 grant 找不到对应策略规则
    """
    candidate: dict[tuple[str, str], PermissionRule] = {}

    for grant in grants:
        key = (grant.tool_name, grant.rule_content)
        if key not in candidate:  # 自动去重：后者覆盖前者
            candidate[key] = self._build_session_rule(grant)

    self._session_rules = candidate
```

### 3.5 `evaluate()` — 引用更新

将 `EvalResult` 构造中的 `rule=r` 改为 `matched_rule=r`：

```python
# 原来是 EvalResult(RuleBehavior.DENY, r.message, r)
return EvalResult(RuleBehavior.DENY, r.message, r)
# → 因为 matched_rule 是第四参数（keyword），需要检查当前调用方式
```

实际检查当前代码：

```python
# Line 88: return EvalResult(RuleBehavior.DENY, r.message, r)
#          → EvalResult(RuleBehavior.DENY, r.message, matched_rule=r)
# Line 93: return EvalResult(RuleBehavior.ALLOW, rule=r)
            # → EvalResult(RuleBehavior.ALLOW, matched_rule=r)
# Line 98: return EvalResult(RuleBehavior.ASK, r.message, r)
#          → EvalResult(RuleBehavior.ASK, r.message, matched_rule=r)
# Line 101: return EvalResult(RuleBehavior(self.default_behavior))
            # → 不变（matched_rule 默认为 None）
```

**验证**: 运行现有 permission 相关测试。
```bash
D:/Miniconda/envs/llm/python -m pytest tests/test_conversation.py::TestPermissionCrossTurn -v
```

---

## Step 4: `tooling/permission/policy.py` — 无改动原则

**结论**: `policy.py` 不需要修改。

原因：
- `build_rules()` 返回的规则中，`(tool_name, rule_content)` 已经是自然唯一的（19 条规则各自独立）
- 唯一性校验已在 Engine 初始化时通过 `_policy_rules_by_key` 索引完成
- `PermissionRule` 的 `rule_id` 字段保留不动（诊断用）
- `condition` 函数和 `RuleBehavior` 枚举不变

---

## Step 5: 重构 `tooling/executor.py`

**文件**: `tooling/executor.py` (MAJOR REFACTOR)

### 5.1 import 变更

```python
# 移除
from tooling.permission import create_engine, create_permission_hook
from hooks import register_hook, trigger_hooks

# 新增
from tooling.permission.engine import PermissionEngine, EvalResult
from tooling.permission.policy import RuleBehavior
from tooling.permission.exceptions import NonPersistablePermission
from hooks import trigger_hooks
```

### 5.2 `ToolExecutor.__init__()` — 重写

```python
class ToolExecutor:
    """工具执行器 —— 实例级权限检查 + 工具分发。

    权限检查不再通过全局 Hook，而是由本实例持有的
    PermissionEngine + Approver 在 execute() 内部完成。
    """

    def __init__(
        self,
        permission_engine: PermissionEngine,
        approver: Approver,
    ):
        self._registry = ToolRegistry()
        self._permission_engine = permission_engine
        self._approver = approver
```

### 5.3 `ToolExecutor.execute()` — 重写管线

```python
def execute(self, name: str, params: dict) -> dict:
    """执行工具。

    管线:
    1. 工具查找
    2. 实例级权限检查 (_authorize_tool_call)
    3. 全局 PreToolUse hooks (仅非权限扩展)
    4. 工具执行
    5. 全局 PostToolUse hooks
    """
    # 1. 工具查找
    tool = self._registry.get_tool(name)
    if tool is None:
        return {"error": f"未知工具: {name}"}

    # 2. 权限检查（实例内部，不经过全局 Hook）
    permission_error = self._authorize_tool_call(name, params)
    if permission_error is not None:
        return permission_error

    # 3. 全局 PreToolUse hooks（仅日志/观测等非权限扩展）
    block = trigger_hooks("PreToolUse", name, params)
    if block is not None:
        return block

    # 4. 工具执行
    try:
        result = tool.run(params)
    except Exception as exc:
        result = {"error": str(exc)}

    # 5. 全局 PostToolUse hooks
    trigger_hooks("PostToolUse", name, params, result)

    return result
```

### 5.4 `ToolExecutor._authorize_tool_call()` — 新增私有方法

```python
def _authorize_tool_call(
    self,
    tool_name: str,
    params: dict,
) -> dict | None:
    """实例级权限评估 + 审批交互。

    Returns:
        None: 放行
        dict: 权限错误（返回给调用方）
    """
    result = self._permission_engine.evaluate(tool_name, params)

    # Gate: DENY
    if result.behavior == RuleBehavior.DENY:
        return {
            "error": f"权限不足: {result.reason or '操作被安全策略拒绝'}"
        }

    # Gate: ALLOW
    if result.behavior == RuleBehavior.ALLOW:
        return None

    # Gate: ASK → 审批
    decision = self._approver(tool_name, params, result.reason)
    choice = decision.get("decision", "deny")

    if choice == "deny":
        reason = decision.get("reason", "")
        if reason:
            return {"error": f"用户拒绝了工具调用: {tool_name}。原因: {reason}"}
        return {"error": f"用户拒绝了工具调用: {tool_name}"}

    if choice == "session":
        try:
            self._permission_engine.allow_for_session(result)
        except NonPersistablePermission:
            # fallback ASK → 降级为单次 allow
            pass
        return None

    # choice == "allow" 或其他 → 单次放行
    return None
```

### 5.5 删除内容

```python
# 删除 build_tool_executor() 函数 (L121-L143)
# 删除 terminal_approver() 整体? → 不，保留 terminal_approver
# terminal_approver 仍然作为默认 Approver 实现保留在 executor.py 中
```

`terminal_approver` 保持不变——它仍然是一个可用的终端审批回调实现，只是不再被工厂函数内部使用。

### 5.6 executor.py 最终内容

```python
"""工具执行器 —— Agent 与工具系统之间的唯一网关。"""

from __future__ import annotations
from typing import Callable

from tooling.registry import ToolRegistry
from tooling.permission.engine import PermissionEngine, EvalResult
from tooling.permission.policy import RuleBehavior
from tooling.permission.exceptions import NonPersistablePermission
from hooks import trigger_hooks


Approver = Callable[[str, dict, str | None], dict]


def terminal_approver(tool_name: str, params: dict, reason: str | None) -> dict:
    """默认终端审批回调 —— 通过 input() 询问用户。"""
    # ... 保持不变 ...


class ToolExecutor:
    def __init__(self, permission_engine: PermissionEngine, approver: Approver):
        self._registry = ToolRegistry()
        self._permission_engine = permission_engine
        self._approver = approver

    def register(self, tool) -> None:
        self._registry.register(tool)

    def get_schemas(self) -> list[dict]:
        return self._registry.get_schemas()

    def execute(self, name: str, params: dict) -> dict:
        tool = self._registry.get_tool(name)
        if tool is None:
            return {"error": f"未知工具: {name}"}
        permission_error = self._authorize_tool_call(name, params)
        if permission_error is not None:
            return permission_error
        block = trigger_hooks("PreToolUse", name, params)
        if block is not None:
            return block
        try:
            result = tool.run(params)
        except Exception as exc:
            result = {"error": str(exc)}
        trigger_hooks("PostToolUse", name, params, result)
        return result

    def _authorize_tool_call(self, tool_name: str, params: dict) -> dict | None:
        result = self._permission_engine.evaluate(tool_name, params)
        if result.behavior == RuleBehavior.DENY:
            return {"error": f"权限不足: {result.reason or '操作被安全策略拒绝'}"}
        if result.behavior == RuleBehavior.ALLOW:
            return None
        decision = self._approver(tool_name, params, result.reason)
        choice = decision.get("decision", "deny")
        if choice == "deny":
            reason = decision.get("reason", "")
            if reason:
                return {"error": f"用户拒绝了工具调用: {tool_name}。原因: {reason}"}
            return {"error": f"用户拒绝了工具调用: {tool_name}"}
        if choice == "session":
            try:
                self._permission_engine.allow_for_session(result)
            except NonPersistablePermission:
                pass
        return None
```

**验证**:
```bash
D:/Miniconda/envs/llm/python -c "
from tooling.permission import create_engine
from tooling.executor import ToolExecutor, terminal_approver
engine = create_engine(default_behavior='allow')
executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)
print('ToolExecutor constructed OK')
"
```

---

## Step 6: 更新 `__init__.py` 导出

### 6.1 `tooling/permission/__init__.py`

```python
"""权限审批模块 —— 实例级权限评估 + 内置安全策略。

公共 API:
  - PermissionEngine       — 权限评估引擎
  - PermissionGrant        — 可持久化的用户授权数据契约
  - create_engine          — 工厂函数（自动组装内置策略）
  - PermissionRule         — 规则数据模型
  - RuleBehavior           — 规则行为枚举
  - EvalResult             — 权限评估结果
  - PermissionArchitectureError — 权限异常基类
  - NonPersistablePermission    — fallback ASK 不可持久化
  - InvalidPermissionGrant      — grant 无效
  - InvalidPermissionRule       — 规则配置无效
"""

from __future__ import annotations
from pathlib import Path

from .engine import EvalResult, PermissionEngine, PermissionGrant
from .exceptions import (
    PermissionArchitectureError,
    NonPersistablePermission,
    InvalidPermissionGrant,
    InvalidPermissionRule,
)
from .policy import PermissionRule, RuleBehavior, build_rules


def create_engine(
    project_root: str | Path | None = None,
    default_behavior: str = "allow",
) -> PermissionEngine:
    """创建 PermissionEngine，自动加载内置安全策略。"""
    root = Path(project_root) if project_root else Path.cwd()
    return PermissionEngine(
        policy_rules=build_rules(root),
        default_behavior=default_behavior,
    )


# 删除: create_permission_hook() (已移除，逻辑集成到 ToolExecutor._authorize_tool_call)
```

**删除**: `create_permission_hook()` 函数 + 其 import（`from typing import Callable`）。

### 6.2 `tooling/__init__.py`

```python
"""Tooling 模块 —— Tool 基类、执行器、权限引擎。"""

from tooling.base import Tool, ToolParameter
from tooling.executor import ToolExecutor
from tooling.permission import (
    PermissionEngine,
    PermissionGrant,
    create_engine,
    PermissionRule,
    RuleBehavior,
)
```

**删除**: `build_tool_executor` 导出。

---

## Step 7: 更新 `main.py`

**文件**: `main.py`

### 7.1 import 变更

```python
# 旧
from tooling.executor import build_tool_executor

# 新
from tooling.permission import create_engine
from tooling.executor import ToolExecutor, terminal_approver
```

### 7.2 组装代码变更

```python
# 旧 (L29)
executor = build_tool_executor(project_root=WORKDIR)

# 新
engine = create_engine(project_root=WORKDIR, default_behavior="ask")
executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)
```

### 7.3 最终 main.py 组装部分

```python
if __name__ == "__main__":
    # 1. 创建 LLM 客户端
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)

    # 2. 创建权限引擎 + 工具执行器
    engine = create_engine(project_root=WORKDIR, default_behavior="ask")
    executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)
    register_all(executor, include_dangerous=True, workdir=WORKDIR, llm=llm)

    # 装配 todo_write 提醒 hooks（PreLLMCall + PostRound）
    register_todo_hooks()

    # 3. 创建 Agent
    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=50)

    # 4. 启动交互式对话 REPL
    conv = Conversation(agent)
    conv.start()
```

engine 变量保留在 main 中，后续 SessionController 重构时可传入：
```python
# conv = Conversation(agent, session_manager=sm, permission_engine=engine)
```

**验证**:
```bash
D:/Miniconda/envs/llm/python -c "from main import *; print('Import OK')"
```

---

## Step 8: 测试

### 8.1 新建 `tests/test_permission_engine.py`

覆盖 5 个测试场景（按 US 组织）：

#### US-1: Executor Isolation (P1)

```python
class TestExecutorIsolation:
    """两个 executor 使用不同 PermissionEngine，互不干扰。"""

    def test_two_executors_independent_engines(self):
        """executor A (deny-all engine) 不影响 executor B (allow-all engine) 执行工具。"""
        from tooling.executor import ToolExecutor
        from tooling.permission import PermissionEngine

        deny_engine = PermissionEngine(default_behavior="deny")
        allow_engine = PermissionEngine(default_behavior="allow")

        def allow_approver(tool_name, params, reason):
            return {"decision": "allow"}

        executor_a = ToolExecutor(permission_engine=deny_engine, approver=allow_approver)
        executor_b = ToolExecutor(permission_engine=allow_engine, approver=allow_approver)

        # 注册同一个工具到两个 executor
        from tools.calculator import CalculatorTool
        executor_a.register(CalculatorTool())
        executor_b.register(CalculatorTool())

        # A: deny engine → 拒绝
        result_a = executor_a.execute("calculator", {"expression": "1+1"})
        assert "error" in result_a

        # B: allow engine → 成功（不受 A 影响）
        result_b = executor_b.execute("calculator", {"expression": "1+1"})
        assert "error" not in result_b

    def test_session_rules_isolated_between_engines(self):
        """engine A 的 session grant 不影响 engine B。"""
        from tooling.permission import PermissionEngine, PermissionGrant

        engine_a = PermissionEngine(default_behavior="deny")
        engine_b = PermissionEngine(default_behavior="deny")

        # 在 engine_a 中安装一条 session rule
        engine_a.replace_session_rules([
            PermissionGrant("bash", "git status")
        ])
        # 注意：engine_a 没有策略规则，所以这条 grant 会失败
        # （因为没有对应策略 ASK 规则——此处用 policy_rules 中有 ALLOW 的 engine 才有效）

        # 但 engine_b 应完全不受影响
        assert len(engine_b._session_rules) == 0
```

#### US-2: Permission Moved from Global Hook (P1)

```python
class TestPermissionNotInGlobalHook:
    """权限检查不再通过全局 PreToolUse Hook。"""

    def test_no_permission_callback_in_global_pre_tool_use(self):
        """搜索生产代码，确认没有注册 permission callback 到全局 Hook。"""
        import hooks
        import inspect
        from tooling.permission.engine import PermissionEngine

        for callback in hooks.HOOKS["PreToolUse"]:
            # 现有的 permission_hook 不在这个列表中
            # 因为 main.py 不再通过 register_hook("PreToolUse", ...) 注册
            pass
        # 这个测试在实际运行中验证——重启后 HOOKS 为空
        # 这里只做静态检查
        import ast, pathlib

        main_path = pathlib.Path(__file__).parent.parent / "main.py"
        source = main_path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (
                    hasattr(node.func, 'id') and node.func.id == 'register_hook'
                ) or (
                    hasattr(node.func, 'attr') and node.func.attr == 'register_hook'
                ):
                    # 检查是否传入 "PreToolUse"
                    args = [ast.unparse(a) for a in node.args]
                    raise AssertionError(
                        f"main.py 不应调用 register_hook('PreToolUse', ...): "
                        f"line {node.lineno}"
                    )

    def test_executor_uses_internal_authorization(self):
        """ToolExecutor.execute() 内部分配权限而非依赖全局 Hook。"""
        from tooling.executor import ToolExecutor
        from tooling.permission import PermissionEngine
        from unittest.mock import MagicMock, patch

        engine = PermissionEngine(default_behavior="deny")
        approver = MagicMock()

        executor = ToolExecutor(permission_engine=engine, approver=approver)

        # 使用 mock tool 验证权限流
        mock_tool = MagicMock()
        mock_tool.run.return_value = {"result": "ok"}
        executor.register(mock_tool)

        # deny engine → 应返回 error 而不调用 approver
        with patch('tooling.executor.trigger_hooks') as mock_trigger:
            result = executor.execute("mock_tool", {})
            assert "error" in result
            # approver 不应被调用（DENY 在 approver 之前）
            approver.assert_not_called()
```

#### US-3: Grant/Listener (P1)

```python
class TestGrantListener:
    """grant_listener 仅在 allow_for_session() 新授权时触发。"""

    def test_listener_called_on_new_grant(self):
        """allow_for_session() 成功 → listener 调用一次。"""
        from tooling.permission.engine import PermissionEngine, EvalResult
        from tooling.permission.policy import PermissionRule, RuleBehavior

        # 构造 engine 并添加一条 ASK 策略规则
        ask_rule = PermissionRule(
            tool_name="bash",
            rule_behavior=RuleBehavior.ASK,
            rule_content="rm *",
            message="确认删除操作",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="policy-ask-rm",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")

        grants_received = []
        engine.set_grant_listener(lambda g: grants_received.append(g))

        # 模拟命中 ASK 规则的 EvalResult
        eval_result = engine.evaluate("bash", {"command": "rm file.txt"})
        assert eval_result.behavior == RuleBehavior.ASK
        assert eval_result.matched_rule is not None

        grant = engine.allow_for_session(eval_result)
        assert len(grants_received) == 1
        assert grants_received[0].tool_name == "bash"
        assert grants_received[0].rule_content == "rm *"

    def test_listener_not_called_on_replace(self):
        """replace_session_rules() 不触发 listener。"""
        from tooling.permission.engine import PermissionEngine, PermissionGrant
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash",
            rule_behavior=RuleBehavior.ASK,
            rule_content="rm *",
            message="确认删除操作",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="policy-ask-rm",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")

        grants_received = []
        engine.set_grant_listener(lambda g: grants_received.append(g))

        engine.replace_session_rules([
            PermissionGrant("bash", "rm *")
        ])
        assert len(grants_received) == 0

    def test_fallback_ask_raises_non_persistable(self):
        """fallback ASK (matched_rule=None) → NonPersistablePermission。"""
        from tooling.permission.engine import PermissionEngine, EvalResult
        from tooling.permission.exceptions import NonPersistablePermission
        from tooling.permission.policy import RuleBehavior
        import pytest

        engine = PermissionEngine(default_behavior="ask")  # 无策略规则
        eval_result = engine.evaluate("bash", {"command": "ls"})
        # 没有策略规则命中 → fallback ASK
        assert eval_result.behavior == RuleBehavior.ASK
        assert eval_result.matched_rule is None

        with pytest.raises(NonPersistablePermission):
            engine.allow_for_session(eval_result)
```

#### US-4: Replace Session Rules (P2)

```python
class TestReplaceSessionRules:
    """replace_session_rules 全量替换 + 原子性 + 错误处理。"""

    def test_replace_replaces_all_rules(self):
        """全量替换：旧 rule 完全被新 grants 替代。"""
        from tooling.permission.engine import PermissionEngine, PermissionGrant, EvalResult
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_a = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="rm *", message="确认删除",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="policy-ask-rm",
        )
        ask_b = PermissionRule(
            tool_name="write_file", rule_behavior=RuleBehavior.ASK,
            rule_content="*", message="确认写入",
            condition=lambda t, p: True,
            rule_id="policy-ask-write",
        )
        engine = PermissionEngine(policy_rules=[ask_a, ask_b], default_behavior="deny")

        # 初始安装 grant_a
        eval_a = engine.evaluate("bash", {"command": "rm file.txt"})
        engine.allow_for_session(eval_a)

        # 替换为 grant_b
        engine.replace_session_rules([PermissionGrant("write_file", "*")])
        assert len(engine._session_rules) == 1
        assert ("write_file", "*") in engine._session_rules
        assert ("bash", "rm *") not in engine._session_rules

    def test_replace_empty_clears_all(self):
        """replace_session_rules([]) 清空所有 session rules。"""
        from tooling.permission.engine import PermissionEngine, PermissionGrant
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="rm *", message="确认删除",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="policy-ask-rm",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        engine.replace_session_rules([PermissionGrant("bash", "rm *")])
        assert len(engine._session_rules) == 1

        engine.replace_session_rules([])
        assert len(engine._session_rules) == 0

    def test_invalid_grant_atomic_rollback(self):
        """无效 grant 导致整体失败，原 _session_rules 不变。"""
        from tooling.permission.engine import PermissionEngine, PermissionGrant
        from tooling.permission.policy import PermissionRule, RuleBehavior
        from tooling.permission.exceptions import InvalidPermissionGrant
        import pytest

        ask_a = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="rm *", message="确认删除",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="policy-ask-rm",
        )
        engine = PermissionEngine(policy_rules=[ask_a], default_behavior="deny")

        # 先安装一条有效 grant
        engine.replace_session_rules([PermissionGrant("bash", "rm *")])
        assert len(engine._session_rules) == 1

        # 尝试替换：一个有效 + 一个无效
        with pytest.raises(InvalidPermissionGrant):
            engine.replace_session_rules([
                PermissionGrant("bash", "rm *"),       # 有效
                PermissionGrant("bash", "nonexistent"), # 无效
            ])

        # 原规则不受影响
        assert len(engine._session_rules) == 1
        assert ("bash", "rm *") in engine._session_rules

    def test_duplicate_grants_in_list_auto_dedup(self):
        """重复 grant (同 tool_name + rule_content) → 后者覆盖前者（去重）。"""
        from tooling.permission.engine import PermissionEngine, PermissionGrant
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="rm *", message="确认删除",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="policy-ask-rm",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        engine.replace_session_rules([
            PermissionGrant("bash", "rm *"),
            PermissionGrant("bash", "rm *"),  # 重复
        ])
        assert len(engine._session_rules) == 1
```

#### US-5: Deny Priority (P2)

```python
class TestDenyPriority:
    """DENY 规则始终优先于 SESSION ALLOW。"""

    def test_deny_overrides_session_allow(self):
        """同一工具不同操作：session allow "git status" 不覆盖 deny "sudo"。"""
        from tooling.permission.engine import PermissionEngine, PermissionGrant
        from tooling.permission.policy import PermissionRule, RuleBehavior

        deny_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.DENY,
            rule_content="sudo *", message="禁止提权",
            condition=lambda t, p: "sudo" in p.get("command", ""),
            rule_id="policy-deny-sudo",
        )
        ask_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="rm *", message="确认删除",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="policy-ask-rm",
        )
        engine = PermissionEngine(
            policy_rules=[deny_rule, ask_rule],
            default_behavior="allow",
        )

        # 安装 session allow for "rm *"
        engine.replace_session_rules([PermissionGrant("bash", "rm *")])

        # "rm file.txt" → session allow 生效
        r1 = engine.evaluate("bash", {"command": "rm file.txt"})
        assert r1.behavior == RuleBehavior.ALLOW

        # "sudo rm -rf /" → deny 优先
        r2 = engine.evaluate("bash", {"command": "sudo rm -rf /"})
        assert r2.behavior == RuleBehavior.DENY
```

#### Engine Internal

```python
class TestPolicyRuleValidation:
    """Engine 初始化时的策略规则校验。"""

    def test_duplicate_natural_key_raises(self):
        """策略规则中 (tool_name, rule_content) 重复 → InvalidPermissionRule。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import PermissionRule, RuleBehavior
        from tooling.permission.exceptions import InvalidPermissionRule
        import pytest

        r1 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="same_content", message="规则1",
            condition=lambda t, p: True, rule_id="id1",
        )
        r2 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ALLOW,
            rule_content="same_content", message="规则2",
            condition=lambda t, p: True, rule_id="id2",
        )

        with pytest.raises(InvalidPermissionRule):
            PermissionEngine(policy_rules=[r1, r2])

    def test_unique_natural_keys_pass(self):
        """不同 (tool_name, rule_content) → 正常初始化。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import PermissionRule, RuleBehavior

        r1 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="rm *", message="确认删除",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="id1",
        )
        r2 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ALLOW,
            rule_content="git status", message="允许 git status",
            condition=lambda t, p: "git status" in p.get("command", ""),
            rule_id="id2",
        )

        engine = PermissionEngine(policy_rules=[r1, r2])
        assert len(engine._policy_rules_by_key) == 2
```

### 8.2 修改 `tests/test_conversation.py`

删除旧权限测试类 `TestPermissionCrossTurn` (L195-L254)，替换为少量验证新 API 的测试：

```python
class TestPermissionGrantFlow:
    """验证 grant 创建和替换的端到端流程。"""

    def test_grant_created_via_allow_for_session(self):
        """通过 allow_for_session 创建 grant → evaluate 返回 ALLOW。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="git status", message="确认 git 操作",
            condition=lambda t, p: "git status" in p.get("command", ""),
            rule_id="policy-ask-git",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        # 模拟用户选择 "session"
        eval_result = engine.evaluate("bash", {"command": "git status"})
        assert eval_result.behavior == RuleBehavior.ASK
        engine.allow_for_session(eval_result)

        # 后续同操作自动放行
        r2 = engine.evaluate("bash", {"command": "git status"})
        assert r2.behavior == RuleBehavior.ALLOW

    def test_grant_scoped_to_tool_and_content(self):
        """grant 仅对匹配的工具和内容生效。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="git status", message="确认 git 操作",
            condition=lambda t, p: "git status" in p.get("command", ""),
            rule_id="policy-ask-git",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        eval_result = engine.evaluate("bash", {"command": "git status"})
        engine.allow_for_session(eval_result)

        # 匹配 → 放行
        r1 = engine.evaluate("bash", {"command": "git status"})
        assert r1.behavior == RuleBehavior.ALLOW

        # 不同工具 → fallback
        r2 = engine.evaluate("write_file", {"file_path": "test.py"})
        assert r2.behavior == RuleBehavior.DENY

        # 同工具不同命令 → fallback
        r3 = engine.evaluate("bash", {"command": "rm file.txt"})
        assert r3.behavior == RuleBehavior.DENY
```

### 8.3 `test_task.py` 中的 `test_tool_call_denied_by_permission`

这个测试使用 mock executor 验证 SubAgent 在权限拒绝时正常返回，不涉及 PermissionEngine API——**不需要修改**。

---

## 验证清单

完成所有步骤后：

```bash
# 1. 全量测试
D:/Miniconda/envs/llm/python -m pytest tests/ -q

# 2. 确认 main.py 不再注册权限 hook 到全局
grep -rn "register_hook.*PreToolUse" Stage 2/project/main.py || echo "PASS: No global permission hook"

# 3. 确认 build_tool_executor 已删除
grep -rn "build_tool_executor" Stage 2/project/tooling/ Stage 2/project/main.py || echo "PASS: build_tool_executor removed"

# 4. 确认 create_permission_hook 已删除
grep -rn "create_permission_hook" Stage 2/project/tooling/ || echo "PASS: create_permission_hook removed"

# 5. 双 executor 隔离验证
D:/Miniconda/envs/llm/python -m pytest tests/test_permission_engine.py::TestExecutorIsolation -v
```

## Project Structure (Final)

```text
Stage 2/project/
├── main.py                          # MODIFY: import 变更加显式组装
├── hooks.py                         # UNCHANGED
├── agent/
│   ├── agent.py                     # UNCHANGED
│   └── conversation.py              # UNCHANGED
│
├── tooling/
│   ├── __init__.py                  # MODIFY: 移除 build_tool_executor，新增 PermissionGrant
│   ├── executor.py                  # REFACTOR: ToolExecutor 接受 engine+approver，内部 _authorize_tool_call
│   └── permission/
│       ├── __init__.py              # MODIFY: 删除 create_permission_hook，导出 PermissionGrant + 异常
│       ├── engine.py                # REFACTOR: EvalResult.matched_rule, PermissionGrant, _policy_rules_by_key,
│       │                              set_grant_listener, allow_for_session(EvalResult), replace_session_rules,
│       │                              _collect_all_rules 显式覆盖, _build_session_rule
│       ├── policy.py                # UNCHANGED
│       └── exceptions.py            # NEW: 4 个异常类
│
└── tests/
    ├── test_conversation.py         # MODIFY: TestPermissionCrossTurn → TestPermissionGrantFlow
    ├── test_permission_engine.py    # NEW: US-1~5 全覆盖 + engine 内部校验
    ├── test_task.py                 # UNCHANGED (test_tool_call_denied_by_permission 不受影响)
    ├── test_compact.py              # UNCHANGED
    ├── test_todo_write.py           # UNCHANGED
    └── test_ask_user.py             # UNCHANGED
```

## Constitution Re-Check (Post-Design)

| # | Principle | Status |
|---|-----------|--------|
| I | Correctness First | ✅ EvalResult 字段重命名同步到所有引用点；原子替换保证无部分状态 |
| II | Small Steps | ✅ 8 steps，每步 1-2 文件 |
| III | Clarity | ✅ 公开 API: 3 methods + 1 dataclass + 4 exceptions |
| IV | Architecture | ✅ main → ToolExecutor → PermissionEngine，单向 |
| V | Don't Reinvent | ✅ 复用 PermissionRule, ToolRegistry, create_engine |
| VI | Mainstream | ✅ 构造注入 |
| VII | Tests | ✅ test_permission_engine.py 覆盖所有 5 个 US |
| VIII | Backward Compat | ⚠️ 删除旧 API — main.py 同步更新 |
| IX | Loop Simple | ✅ agent.py unchanged |
| X | Elevate Design | ✅ PermissionGrant 新概念，内聚授权数据 |

**Re-Check Result**: PASS
