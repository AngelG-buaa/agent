"""Agent 核心循环 —— Think → Act → Observe。"""

import json
from time import sleep

from agent.llm_client import LLMClient
from tooling.executor import ToolExecutor
from agent.message_utils import filter_assistant_message
from hooks import trigger_hooks


class Agent:
    """最小 Agent：接收用户输入，循环调用 LLM + 工具，返回最终答案。"""

    def __init__(self, llm: LLMClient, executor: ToolExecutor,
                 system_prompt: str | None = None, max_steps: int = 10):
        self.llm = llm
        self.executor = executor
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    def run(self, user_input: str) -> str:
        """核心循环：tool_calls → 执行工具 → 回传；stop → 返回答案。"""
        messages: list[dict] = []
        messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_input})

        # Hook: 用户输入提交（日志、上下文注入等）
        trigger_hooks("UserPromptSubmit", user_input)

        for _ in range(self.max_steps):
            stop_reason, msg = self.llm.chat(messages, self.executor.get_schemas())
            sleep(10)

            if stop_reason == "tool_calls":
                # messages.append(filter_assistant_message(msg))
                messages.append(msg)
                self._execute_tool_calls(msg.tool_calls, messages)
            else:
                trigger_hooks("PreAgentStop", messages)
                return msg.content or "（模型未返回文本）"

        trigger_hooks("PreAgentStop", messages)
        return "Agent 已停止：达到最大步数限制。"

    def _execute_tool_calls(self, tool_calls, messages: list) -> None:
        """执行 msg 中的所有 tool_calls，结果以 role='tool' 追加到 messages。"""
        for tc in tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  🔧 调用工具: {name}({args})")

            result = self.executor.execute(name, args)
            print(f"  ✅ 结果: {result}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
