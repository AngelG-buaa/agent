"""Agent 核心循环 —— Think → Act → Observe。"""

import json

from llm_client import LLMClient
from tool_registry import ToolRegistry


class Agent:
    """最小 Agent：接收用户输入，循环调用 LLM + 工具，返回最终答案。"""

    def __init__(self, llm: LLMClient, tool_registry: ToolRegistry, max_steps: int = 10):
        self.llm = llm
        self.tool_registry = registry
        self.max_steps = max_steps

    def run(self, user_input: str) -> str:
        """核心循环：tool_calls → 执行工具 → 回传；stop → 返回答案。"""
        messages = [{"role": "user", "content": user_input}]

        for _ in range(self.max_steps):
            stop_reason, msg = self.llm.chat(messages, self.tool_registry.get_schemas())

            if stop_reason == "tool_calls":
                messages.append(msg)
                self._execute_tool_calls(msg, messages)
            else:
                return msg.content or "（模型未返回文本）"

        return "Agent 已停止：达到最大步数限制。"

    def _execute_tool_calls(self, msg, messages: list) -> None:
        """执行 msg 中的所有 tool_calls，结果以 role='tool' 追加到 messages。"""
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  🔧 调用工具: {name}({args})")

            result = self.registry.execute(name, args)
            print(f"  ✅ 结果: {result}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
