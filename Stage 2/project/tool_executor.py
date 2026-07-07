"""工具执行器 —— 代理 ToolRegistry，在调用链上插入权限检查。

设计原则:
  - 与 ToolRegistry 同接口（execute + get_schemas），Agent 靠 duck typing 工作
  - I/O 逻辑（_prompt_user）在此模块中，PermissionEngine 保持纯逻辑
  - PreToolUse/PostToolUse hooks 只能观察，不能否决执行
"""

import json
from typing import Callable

from config import WORKDIR
from tool_permission import PermissionEngine
from tool_registry import ToolRegistry


# ============================================================
# 🔮 预留扩展: PostToolUse 审计日志
# 用途: 将每次权限判断结果 + 工具执行结果写入审计日志文件
# 当前: post_hooks 参数已预留，默认传空列表
# 实现时: 创建一个 AuditLogHook，通过 post_hooks 注入，
#         每次工具执行后写入 JSONL 格式日志行:
#         {"ts": "...", "tool": "...", "params": {...}, "behavior": "...", "result": "..."}
# ============================================================


class ToolExecutor:
    """工具执行器 —— 在 ToolRegistry 外层包装权限检查。

    用法:
        engine = PermissionEngine(sources=[...], tool_registry=registry)
        executor = ToolExecutor(registry, engine)
        agent = Agent(llm, executor, ...)  # 替代原来的 registry
    """

    def __init__(
        self,
        registry: ToolRegistry,
        engine: PermissionEngine,
        pre_hooks: list[Callable[[str, dict], None]] | None = None,
        post_hooks: list[Callable[[str, dict, dict], None]] | None = None,
    ):
        self._registry = registry
        self._engine = engine
        self._pre_hooks = pre_hooks or []
        self._post_hooks = post_hooks or []

    # ---- 公开接口（与 ToolRegistry 兼容）----

    def get_schemas(self) -> list[dict]:
        """代理到 ToolRegistry.get_schemas()。"""
        return self._registry.get_schemas()

    def execute(self, name: str, params: dict) -> dict:
        """执行工具（带权限检查）。

        流程: PreToolUse hooks → 权限评估 → [审批] → 执行 → PostToolUse hooks
        """
        # 1. PreToolUse hooks（纯观察）
        for hook in self._pre_hooks:
            try:
                hook(name, params)
            except Exception:
                pass  # hook 异常不影响主流程

        # 2. 权限评估
        behavior, reason = self._engine.evaluate(name, params)

        if behavior == "deny":
            msg = f"权限不足: {reason or '操作被安全策略拒绝'}"
            print(f"\n  🛑 {msg}")
            print(f"     工具: {name}({self._format_params(params)})")
            result = {"error": msg}
            self._run_post_hooks(name, params, result)
            return result

        if behavior == "ask":
            if not self._prompt_user(name, params, reason):
                msg = f"用户拒绝了工具调用: {name}"
                result = {"error": msg}
                self._run_post_hooks(name, params, result)
                return result

        # 3. 执行工具
        result = self._registry.execute(name, params)

        # 4. PostToolUse hooks
        self._run_post_hooks(name, params, result)

        return result

    # ---- 内部方法 ----

    def _prompt_user(self, tool_name: str, params: dict, reason: str | None) -> bool:
        """终端交互式审批（I/O 逻辑集中在此）。"""
        print()
        print(f"  ⚠  权限确认: {reason or '需要用户审批'}")
        print(f"     工具: {tool_name}({self._format_params(params)})")
        try:
            choice = input("     允许执行？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return choice in ("y", "yes")

    def _run_post_hooks(self, name: str, params: dict, result: dict) -> None:
        for hook in self._post_hooks:
            try:
                hook(name, params, result)
            except Exception:
                pass

    @staticmethod
    def _format_params(params: dict) -> str:
        """格式化参数用于终端显示，长值截断。"""
        parts = []
        for k, v in params.items():
            s = str(v)
            if len(s) > 80:
                s = s[:77] + "..."
            parts.append(f"{k}={s!r}")
        return ", ".join(parts)
