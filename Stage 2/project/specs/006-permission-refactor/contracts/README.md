# Public API Contracts: Permission 系统实例级重构

**Date**: 2026-07-13

## 1. PermissionEngine — Public Interface

```python
class PermissionEngine:
    # === 构造 ===
    def __init__(
        self,
        policy_rules: list[PermissionRule] | None = None,
        default_behavior: str = "allow",
    ) -> None: ...

    # === Grant Listener ===
    def set_grant_listener(
        self,
        listener: Callable[[PermissionGrant], None] | None,
    ) -> None:
        """安装/移除 grant 持久化回调。仅在新 grant 产生时调用。"""
        ...

    # === 用户交互产生授权 ===
    def allow_for_session(self, result: EvalResult) -> PermissionGrant:
        """从 ASK 评估结果创建一条持久化 grant。

        Preconditions:
        - result.behavior == RuleBehavior.ASK
        - result.matched_rule is not None
        - result.matched_rule.rule_id is not None
        - result.matched_rule 属于当前 engine

        Postconditions:
        - 返回 PermissionGrant(tool_name, rule_content)
        - 如果 listener 已安装: listener(grant) 成功 → 安装运行时 rule
        - 如果 listener 抛异常: 不安装 rule, 异常向上传播

        Errors:
        - ValueError: result.behavior != ASK
        - NonPersistablePermission: matched_rule is None (fallback ASK)
        - InvalidPermissionRule: matched_rule 不属于当前 engine 或 rule_id 缺失
        """
        ...

    # === 全量替换 ===
    def replace_session_rules(self, grants: list[PermissionGrant]) -> None:
        """替换当前所有会话规则。

        Preconditions:
        - 每个 grant 的 (tool_name, rule_content) 必须对应一条已有策略规则

        Postconditions:
        - self._session_rules 被完全替换
        - 不触发 grant_listener

        Errors:
        - InvalidPermissionGrant: 任一 grant 找不到对应策略规则
        - 原子性: 所有 grant 验证通过后才替换；任一失败则保持原状
        """
        ...

    # === 权限评估 ===
    def evaluate(self, tool_name: str, params: dict) -> EvalResult:
        """评估工具调用权限。

        Pipeline: DENY → ALLOW (session + policy) → ASK (uncovered) → fallback
        """
        ...
```

## 2. ToolExecutor — Public Interface

```python
class ToolExecutor:
    def __init__(
        self,
        permission_engine: PermissionEngine,
        approver: Approver,
    ) -> None:
        """构造执行器。

        Args:
            permission_engine: 权限评估引擎（实例独占）
            approver: 审批回调，签名 (tool_name, params, reason) -> decision_dict
        """
        ...

    def execute(self, name: str, params: dict) -> dict:
        """执行工具。

        Pipeline:
        1. 工具查找 (ToolRegistry)
        2. 权限检查 (_authorize_tool_call)
        3. 全局 PreToolUse hooks (non-permission only)
        4. 工具执行 (tool.run)
        5. 全局 PostToolUse hooks
        """
        ...

    def register(self, tool) -> None:
        """注册工具实例（同名覆盖）。"""
        ...

    def get_schemas(self) -> list[dict]:
        """导出所有工具的 API schema。"""
        ...
```

## 3. Factory — Public Interface

```python
# tooling/permission/__init__.py

def create_engine(
    project_root: str | Path | None = None,
    default_behavior: str = "allow",
) -> PermissionEngine:
    """创建 PermissionEngine，自动加载内置安全策略。

    Args:
        project_root: 项目根目录（安全边界），None 则使用 cwd
        default_behavior: 所有规则未命中时的默认行为 (allow/deny/ask)
    """
    ...
```

## 4. Exceptions — Public Interface

```python
# tooling/permission/exceptions.py

class PermissionArchitectureError(Exception):
    """权限模块基础异常。"""

class NonPersistablePermission(PermissionArchitectureError):
    """当前授权决策无法持久化（fallback ASK）。"""

class InvalidPermissionGrant(PermissionArchitectureError):
    """持久化 grant 无法映射到现有策略规则。"""

class InvalidPermissionRule(PermissionArchitectureError):
    """策略规则配置无效（自然键重复等）。"""
```

## 5. Data Types — Public Interface

```python
@dataclass(frozen=True)
class PermissionGrant:
    tool_name: str
    rule_content: str

@dataclass
class EvalResult:
    behavior: RuleBehavior          # DENY | ALLOW | ASK
    reason: str | None = None
    matched_rule: PermissionRule | None = None  # None = fallback

Approver = Callable[[str, dict, str | None], dict]
# Returns: {"decision": "allow"|"deny"|"session", "reason"?: str}
```

## 6. Composition Root (main.py)

```python
# main.py — 推荐的显式组装方式
from tooling.permission import create_engine
from tooling.executor import ToolExecutor, terminal_approver

engine = create_engine(project_root=WORKDIR, default_behavior="ask")
executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)
register_all(executor, include_dangerous=True, workdir=WORKDIR, llm=llm)

# engine 可以同时传给 SessionController (未来)
# conversation = Conversation(agent, session_manager, permission_engine=engine)
```

## Deleted APIs

以下旧接口在本次重构中删除：

| API | 原位置 | 替代方案 |
|-----|--------|---------|
| `build_tool_executor()` | `tooling/executor.py` | `main.py` 显式构造 |
| `create_permission_hook()` | `tooling/permission/__init__.py` | `ToolExecutor._authorize_tool_call()` |
| `engine.revoke_session_rule(rule_id)` | `PermissionEngine` | `engine.replace_session_rules(grants)` |
| `engine.clear_session_rules()` | `PermissionEngine` | `engine.replace_session_rules([])` |
| `EvalResult.rule` | `EvalResult` | `EvalResult.matched_rule` |
