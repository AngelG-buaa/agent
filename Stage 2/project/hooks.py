"""Hook 系统 —— Agent 行为扩展的注册/触发机制。

所有 Hook 事件按固定顺序触发。任何回调返回非 None 的 dict 即中断链路，
该 dict 作为工具结果注入对话（用于 PreToolUse/UserPromptSubmit）。

事件一览:
  - SessionStart            Agent 初始化完成
  - UserPromptSubmit        用户输入后、LLM 调用前
  - PreLLMCall              每轮 LLM 调用前（可注入消息到对话中）
  - PreToolUse              工具执行前
  - PostToolUse             工具执行后
  - PostRound               每轮结束后（含 stop_reason 和 tool_calls 信息）
  - PreAgentStop            Agent 准备返回结果前
"""

from __future__ import annotations
from typing import Callable

# Hook 回调签名: (*args) -> dict | None
HookCallback = Callable[..., dict | None]

HOOKS: dict[str, list[HookCallback]] = {
    "SessionStart": [],
    "UserPromptSubmit": [],
    "PreLLMCall": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "PostRound": [],
    "PreAgentStop": [],
}


def register_hook(event: str, callback: HookCallback) -> None:
    """注册 hook 回调。回调按注册顺序依次触发。"""
    if event not in HOOKS:
        raise ValueError(f"未知的 hook 事件: {event}")
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args) -> dict | None:
    """触发事件。返回第一个非 None 的结果；全部通过则返回 None。"""
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None
