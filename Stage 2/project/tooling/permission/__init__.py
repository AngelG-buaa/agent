"""权限审批模块 —— 3 步管线 + 内置安全策略。

公共 API:
  - PermissionEngine       — 权限评估引擎（含会话规则 allow_for_session / revoke_session_rule）
  - create_engine          — 工厂函数（自动组装内置策略）
  - create_permission_hook — 工厂函数（创建 PreToolUse hook 回调）
  - PermissionRule         — 规则数据模型
  - RuleBehavior           — 规则行为枚举
"""

from __future__ import annotations
from pathlib import Path
from typing import Callable

from .engine import EvalResult, PermissionEngine
from .policy import PermissionRule, RuleBehavior, build_rules


def create_engine(
    project_root: str | Path | None = None,
    default_behavior: str = "allow",
) -> PermissionEngine:
    """创建 PermissionEngine，自动加载内置安全策略。

    Args:
        project_root: 项目根目录（安全边界 + 策略配置目录），None 则使用 cwd
        default_behavior: 所有规则未命中时的默认行为
    """
    root = Path(project_root) if project_root else Path.cwd()
    return PermissionEngine(
        policy_rules=build_rules(root),
        default_behavior=default_behavior,
    )


def create_permission_hook(
    engine: PermissionEngine,
    approver,
) -> Callable[..., dict | None]:
    """创建 PreToolUse hook 回调，封装权限检查 + 审批逻辑。

    Args:
        engine: 权限评估引擎
        approver: 审批回调 (tool_name, params, reason) -> "allow" | "deny" | "session"

    Returns:
        hook 回调: (tool_name, params) -> dict | None
          - 返回 dict（error）阻断工具执行
          - 返回 None 放行
    """
    def permission_hook(tool_name: str, params: dict) -> dict | None:
        result = engine.evaluate(tool_name, params)

        if result.behavior == RuleBehavior.DENY:
            return {"error": f"权限不足: {result.reason or '操作被安全策略拒绝'}"}

        if result.behavior == RuleBehavior.ASK:
            decision = approver(tool_name, params, result.reason)
            if decision == "deny":
                return {"error": f"用户拒绝了工具调用: {tool_name}"}
            if decision == "session" and result.rule:
                engine.allow_for_session(
                    tool_name, result.rule.rule_content, result.reason or "",
                )

        # allow / session 已处理 / fallback → 放行
        return None

    return permission_hook
