"""工具执行器 —— 代理 ToolRegistry，在调用链上插入权限检查。

设计原则:
  - 与 ToolRegistry 同接口（execute + get_schemas），Agent 靠 duck typing 工作
  - 审批 I/O 通过 approver 回调注入（默认终端 input），可替换为 Web/GUI/静默
  - 工厂函数 build_tool_executor 封装所有组装细节
"""

from pathlib import Path
from typing import Callable

from tooling.registry import ToolRegistry
from tooling.permission import PermissionEngine, create_engine


# ---- 审批回调类型 ----

Approver = Callable[[str, dict, str | None], bool]
"""审批回调签名: (tool_name, params, reason) -> approved

返回 True 表示批准执行，False 表示拒绝。
"""


def terminal_approver(tool_name: str, params: dict, reason: str | None) -> bool:
    """默认终端审批回调 —— 通过 input() 询问用户。

    TODO: 审批三态 (本次允许 / 始终允许 / 拒绝)
          当前只支持 True/False。后续扩展为三态时:
            - 选"始终允许" → executor._engine.session.add(allow_rule)
            - Approver 签名需改为返回 ApprovalDecision 枚举
    """
    print()
    print(f"  ⚠  权限确认: {reason or '需要用户审批'}")
    print(f"     工具: {tool_name}({_format_params(params)})")
    try:
        choice = input("     允许执行？[y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return choice in ("y", "yes")


class ToolExecutor:
    """工具执行器 —— 在 ToolRegistry 外层包装权限检查。

    用法:
        engine = create_engine(project_root)
        executor = ToolExecutor(registry, engine)
        agent = Agent(llm, executor, ...)
    """

    def __init__(
        self,
        registry: ToolRegistry,
        engine: PermissionEngine,
        approver: Approver | None = None,
    ):
        self._registry = registry
        self._engine = engine
        self._approver = approver or terminal_approver

    # ---- 公开接口（与 ToolRegistry 兼容）----

    def get_schemas(self) -> list[dict]:
        return self._registry.get_schemas()

    def execute(self, name: str, params: dict) -> dict:
        """执行工具（带权限检查）。

        流程: 权限评估 → [审批] → 执行
        """
        tool = self._registry.get_tool(name)
        behavior, reason = self._engine.evaluate(name, params, tool=tool)

        if behavior == "deny":
            return {"error": f"权限不足: {reason or '操作被安全策略拒绝'}"}

        if behavior == "ask":
            # TODO: 审批三态 (本次允许 / 始终允许 / 拒绝)
            # 当前只支持 True/False
            if not self._approver(name, params, reason):
                return {"error": f"用户拒绝了工具调用: {name}"}

        # allow / default → 放行
        return self._registry.execute(name, params)


# ---- 辅助 ----

def _format_params(params: dict) -> str:
    parts = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def build_tool_executor(
    registry: ToolRegistry,
    project_root: str | Path | None = None,
    default_behavior: str = "allow",
    approver: Approver | None = None,
) -> ToolExecutor:
    """一键组装 ToolExecutor。

    封装了 PermissionEngine 创建、Source 组装等所有内部细节。
    调用方只需提供 ToolRegistry，返回可注入 Agent 的 executor。

    Args:
        registry: 工具注册表
        project_root: 项目根目录，None 则使用 cwd
        default_behavior: 未命中时的默认行为
        approver: 审批回调，None 则使用终端 input()
    """
    engine = create_engine(
        project_root=project_root,
        default_behavior=default_behavior,
    )
    return ToolExecutor(registry, engine, approver=approver)
