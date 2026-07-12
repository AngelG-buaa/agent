"""Agent 核心循环 —— Think → Act → Observe。"""

import json
from time import sleep
from typing import Callable

from agent.llm_client import LLMClient
from tooling.executor import ToolExecutor
from agent.utils import default_print_handler
from agent.compact import compact_pipeline
from hooks import trigger_hooks


class Agent:
    """最小 Agent：接收用户输入，循环调用 LLM + 工具，返回最终答案。"""

    def __init__(self, llm: LLMClient, executor: ToolExecutor,
                 system_prompt: str | None = None, max_steps: int = 10,
                 tool_filter: set[str] | None = None,
                 print_handler: Callable | None = None):
        self.llm = llm
        self.executor = executor
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.tool_filter = tool_filter
        self.print_handler = print_handler or default_print_handler

    def run(self, messages: list[dict]) -> str:
        """核心循环：Think → Act → Observe，通过 hooks 扩展行为。

        messages 必须已包含 system prompt（如需要）和最新的 user 消息。
        循环体会原地修改 messages（追加 assistant 和 tool 消息）。
        """
        for _ in range(self.max_steps):
            # Hook: 每轮 LLM 调用前，允许 hooks 注入额外消息（todo 提醒）
            inject = trigger_hooks("PreLLMCall")
            if inject:
                messages.extend(inject["messages"])

            # Compact: 四层渐进式上下文压缩（L3→L1→L2→L4）
            compact_pipeline(messages, self.llm)

            schemas = self.executor.get_schemas()
            if self.tool_filter:
                schemas = [s for s in schemas
                           if s["function"]["name"] not in self.tool_filter]

            stop_reason, msg = self.llm.chat(messages, schemas)
            # sleep(30)

            if stop_reason == "tool_calls":
                # messages.append(filter_assistant_message(msg))
                messages.append(msg)
                self._execute_tool_calls(msg.tool_calls, messages)

            # Hook: 每轮结束后通知 hooks（更新todo记录）
            tool_calls = msg.tool_calls if stop_reason == "tool_calls" else None
            trigger_hooks("PostRound", stop_reason, tool_calls)

            if stop_reason != "tool_calls":
                trigger_hooks("PreAgentStop", messages)
                return msg.content or "（模型未返回文本）"

        trigger_hooks("PreAgentStop", messages)
        return "Agent 已停止：达到最大步数限制。"

    def _execute_tool_calls(self, tool_calls, messages: list) -> None:
        """执行 msg 中的所有 tool_calls，结果以 role='tool' 追加到 messages。"""
        for tc in tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            self.print_handler(name, args)

            result = self.executor.execute(name, args)
            # print(f"  ✅ 结果: {result}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })


class SubAgent(Agent):
    """子代理：独立上下文、受限工具集、30 轮限制、精简输出。

    SubAgent 是 Agent 的特殊化——通过 __init__ 集中所有 Sub-Agent 专属配置
    （提示词、工具过滤、输出格式、步数限制），并通过覆盖 _execute_tool_calls()
    实现轮数跟踪和提醒注入，不触碰 Agent.run() 循环体。

    用法:
        sub = SubAgent(llm=main_agent.llm, executor=main_agent.executor)
        result = sub.run("完成某个子任务的描述")
    """

    def __init__(self, llm: LLMClient, executor: ToolExecutor):
        from agent.prompts import SUB_SYSTEM_PROMPT
        from agent.utils import sub_print_handler

        super().__init__(
            llm=llm,
            executor=executor,
            system_prompt=SUB_SYSTEM_PROMPT,
            max_steps=30,
            tool_filter={"task", "todo_write"},
            print_handler=sub_print_handler,
        )
        self._round = 0

    def _execute_tool_calls(self, tool_calls, messages: list) -> None:
        """覆盖父类：跟踪轮数，第 30 轮时注入提醒。

        提醒在第 30 轮工具调用前注入到 messages 中，SubAgent 的 LLM
        会在同一轮内看到它并应返回文本回复。若 LLM 仍返回 tool_calls，
        max_steps=30 的硬截断作为兜底——循环自然结束，返回最后的消息文本。
        """
        self._round += 1
        if self._round == 30:
            messages.append({
                "role": "user",
                "content": "你已达到最大轮数限制，请基于已有信息给出当前最佳结论。",
            })
        super()._execute_tool_calls(tool_calls, messages)
