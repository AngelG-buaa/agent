"""工具执行器 —— Agent 与工具系统之间的唯一网关。

职责:
  - 工具注册/管理   —— 内部持有 ToolRegistry
  - 实例级权限检查   —— 通过构造注入的 PermissionEngine + Approver 在 execute() 内部完成
  - 全局 Hook 触发   —— PreToolUse / PostToolUse（仅承载日志、观测等非权限扩展）
  - 对外接口        —— register() / get_schemas() / execute()
"""
from __future__ import annotations
from typing import Callable

from tooling.registry import ToolRegistry
from tooling.permission.engine import PermissionEngine
from tooling.permission.policy import RuleBehavior
from tooling.permission.exceptions import NonPersistablePermission
from hooks import trigger_hooks


# ═══════════════════════════════════════════════════════════════
# Approver 回调类型
# ═══════════════════════════════════════════════════════════════

Approver = Callable[[str, dict, str | None], dict]


def terminal_approver(tool_name: str, params: dict, reason: str | None) -> dict:
    """默认终端审批回调 —— 通过 input() 询问用户。

    返回: {"decision": "allow"|"deny"|"session", "reason"?: str}
      - "session": 添加会话 ALLOW 规则，本次及后续同操作不再询问
      - "deny" 时可附带拒绝原因（用户可选输入）
    """
    print()
    print(f"  ⚠  权限确认: {reason or '需要用户审批'}")
    print(f"     工具: {tool_name}({params})")
    try:
        choice = input("     [y]允许 [n]拒绝 [a]始终允许? ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return {"decision": "deny"}
    if choice in ("a", "always"):
        return {"decision": "session"}
    if choice in ("y", "yes"):
        return {"decision": "allow"}

    # 拒绝时可输入原因
    deny_reason = input("     输入拒绝原因（可选，回车跳过）: ").strip()
    result: dict = {"decision": "deny"}
    if deny_reason:
        result["reason"] = deny_reason
    return result


# ═══════════════════════════════════════════════════════════════
# ToolExecutor
# ═══════════════════════════════════════════════════════════════


class ToolExecutor:
    """工具执行器 —— 实例级权限检查 + 工具分发。

    权限检查不再通过全局 Hook，而是由本实例持有的
    PermissionEngine + Approver 在 execute() 内部完成。

    用法:
        engine = create_engine(project_root=WORKDIR, default_behavior="ask")
        executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)
        executor.register(BashTool(...))
        agent = Agent(llm, executor, ...)
    """

    def __init__(
        self,
        permission_engine: PermissionEngine,
        approver: Approver,
    ):
        self._registry = ToolRegistry()
        self._permission_engine = permission_engine
        self._approver = approver

    # ---- 工具注册 ----

    def register(self, tool) -> None:
        """注册工具实例（同名覆盖）。"""
        self._registry.register(tool)

    # ---- 公开接口（Agent 调用）----

    def get_schemas(self) -> list[dict]:
        """导出所有工具的 API schema。"""
        return self._registry.get_schemas()

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

    # ---- 私有方法 ----

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

        if choice not in ("allow", "session"):
            return {
                "error": f"无效的权限审批结果: {choice!r}"
            }

        if choice == "session":
            try:
                self._permission_engine.allow_for_session(result)
            except NonPersistablePermission:
                # fallback ASK → 降级为单次 allow
                pass

        # choice == "allow" → 单次放行
        return None
