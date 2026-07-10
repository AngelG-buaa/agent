"""工具执行器 —— Agent 与工具系统之间的唯一网关。

职责:
  - 工具注册/管理   —— 内部持有 ToolRegistry
  - Hook 触发       —— PreToolUse / PostToolUse（权限检查是 PreToolUse 的一个注册回调）
  - 审批交互        —— 通过 Approver 回调注入，默认终端 input()
  - 对外接口        —— register() / get_schemas() / execute()

工厂函数 build_tool_executor() 封装了 engine + hook 注册 + executor 的组装细节。
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable

from tooling.registry import ToolRegistry
from tooling.permission import create_engine, create_permission_hook
from hooks import register_hook, trigger_hooks


# ═══════════════════════════════════════════════════════════════
# ToolExecutor
# ═══════════════════════════════════════════════════════════════


class ToolExecutor:
    """工具执行器 —— Hook 触发 + 工具分发。

    不直接持有 PermissionEngine。权限检查通过 PreToolUse hook 回调实现。
    build_tool_executor() 工厂负责在构造后将权限 hook 注册到 PreToolUse。

    用法:
        executor = build_tool_executor(project_root=WORKDIR)
        executor.register(BashTool(...))
        agent = Agent(llm, executor, ...)
    """

    def __init__(self):
        self._registry = ToolRegistry()

    # ---- 工具注册 ----

    def register(self, tool) -> None:
        """注册工具实例（同名覆盖）。"""
        self._registry.register(tool)

    # ---- 公开接口（Agent 调用）----

    def get_schemas(self) -> list[dict]:
        """导出所有工具的 API schema。"""
        return self._registry.get_schemas()

    def execute(self, name: str, params: dict) -> dict:
        """执行工具（通过 hooks 链）。

        流程: PreToolUse hooks → 工具查找 → 执行 → PostToolUse hooks
        PreToolUse 中任一 hook 返回非 None 的 dict → 阻断执行，返回该 dict 作为错误。
        """
        # Gate: PreToolUse hooks（权限检查、日志、MCP 拦截等）
        block = trigger_hooks("PreToolUse", name, params)
        if block is not None:
            return block

        # 工具查找
        tool = self._registry.get_tool(name)
        if tool is None:
            return {"error": f"未知工具: {name}"}

        # 执行
        try:
            result = tool.run(params)
        except Exception as exc:
            result = {"error": str(exc)}

        # Gate: PostToolUse hooks（日志、副作用、session 记录等）
        trigger_hooks("PostToolUse", name, params, result)

        return result


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

    内部: 创建 PermissionEngine → 包装为 PreToolUse hook → 注册 → 返回简化 executor。

    Args:
        project_root: 项目根目录（安全边界 + 策略配置目录），None 则使用 cwd
        default_behavior: 所有规则未命中时的默认行为
        approver: 审批回调，None 则使用终端 input()
    """
    engine = create_engine(
        project_root=project_root,
        default_behavior=default_behavior,
    )
    # 将权限检查注册为 PreToolUse 的第一个回调
    permission_hook = create_permission_hook(engine, approver)
    register_hook("PreToolUse", permission_hook)

    return ToolExecutor()
