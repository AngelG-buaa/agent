"""工具执行器 —— Agent 与工具系统之间的唯一网关。

职责:
  - 工具注册/管理   —— 内部持有 ToolRegistry
  - 权限评估        —— 集成 PermissionEngine（3 步管线）
  - 审批交互        —— 通过 Approver 回调注入，默认终端 input()
  - 对外接口        —— register() / get_schemas() / execute()

工厂函数 build_tool_executor() 封装了 engine + executor 的组装细节。
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable

from tooling.registry import ToolRegistry
from tooling.permission import PermissionEngine, RuleBehavior, create_engine


# ═══════════════════════════════════════════════════════════════
# ToolExecutor
# ═══════════════════════════════════════════════════════════════


class ToolExecutor:
    """工具执行器 —— 权限检查 + 工具分发。

    用法:
        engine = create_engine(project_root)
        executor = ToolExecutor(engine)
        executor.register(BashTool(...))
        agent = Agent(llm, executor, ...)
    """

    def __init__(
        self,
        engine: PermissionEngine,
        approver: Approver,
    ):
        # ── 工具注册层 ──
        self._registry = ToolRegistry()

        # ── 权限评估层 ──
        self._engine = engine

        # ── 审批交互层 ──
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
        """执行工具（带权限检查）。

        流程: 权限评估 → [审批] → 工具查找 → 执行
        """
        result = self._engine.evaluate(name, params)

        if result.behavior == RuleBehavior.DENY:
            return {"error": f"权限不足: {result.reason or '操作被安全策略拒绝'}"}

        if result.behavior == RuleBehavior.ASK:
            decision = self._approver(name, params, result.reason)
            if decision == "deny":
                return {"error": f"用户拒绝了工具调用: {name}"}
            if decision == "session" and result.rule:
                self._engine.allow_for_session(
                    name, result.rule.rule_content, result.reason or "",
                )

        # allow / session / default → 放行
        tool = self._registry.get_tool(name)
        if tool is None:
            return {"error": f"未知工具: {name}"}
        try:
            return tool.run(params)
        except Exception as exc:
            return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════════
# 审批回调
# ═══════════════════════════════════════════════════════════════

# 审批回调: (tool_name, params, reason) -> "allow" | "deny" | "session"
Approver = Callable[[str, dict, str | None], str]


def terminal_approver(tool_name: str, params: dict, reason: str | None) -> str:
    """默认终端审批回调 —— 通过 input() 询问用户。

    返回: "allow" | "deny" | "session"
      - "session": 添加会话 ALLOW 规则，本次及后续同操作不再询问
    """
    print()
    print(f"  ⚠  权限确认: {reason or '需要用户审批'}")
    print(f"     工具: {tool_name}({params})")
    try:
        choice = input("     [y]允许 [n]拒绝 [a]始终允许? ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "deny"
    if choice in ("a", "always"):
        return "session"
    return "allow" if choice in ("y", "yes") else "deny"


# ═══════════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════════


def build_tool_executor(
    project_root: str | Path | None = None,
    default_behavior: str = "ask",
    approver: Approver = terminal_approver,
) -> ToolExecutor:
    """一键组装 ToolExecutor。

    封装了 PermissionEngine 创建、PolicySettingsSource 组装等内部细节。

    Args:
        project_root: 项目根目录（安全边界 + 策略配置目录），None 则使用 cwd
        default_behavior: 所有规则未命中时的默认行为
        approver: 审批回调，None 则使用终端 input()
    """
    engine = create_engine(
        project_root=project_root,
        default_behavior=default_behavior,
    )
    return ToolExecutor(engine, approver=approver)
