"""Agent 核心循环 —— Think → Act → Observe。"""

import json
from time import sleep
from typing import Callable

from agent.llm_client import LLMClient
from tooling.executor import ToolExecutor
from agent.utils import default_print_handler, normalize_message
from agent.compact import compact_pipeline
from hooks import trigger_hooks


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


def _emit_message(
    msg: dict,
    messages: list,
    on_message: Callable[[dict], None] | None,
) -> None:
    """将消息通过 sink 加入列表。

    on_message 是完整 sink：持久化模式由 Controller 完成
    「SQLite 写一次 → active.messages 追加一次」；
    Agent 不再自己追加。

    on_message 为 None 时（SubAgent / Transient），Agent 直接 append。
    """
    if on_message is not None:
        on_message(msg)        # sink 负责持久化 + 内存追加
    else:
        messages.append(msg)   # 默认：仅内存追加


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

    def run(
        self,
        messages: list[dict],
        on_message: Callable[[dict], None] | None = None,
    ) -> str:
        """核心循环：Think → Act → Observe。

        Args:
            messages: working context，必须已包含 system 和最新的 user 消息。
            on_message: 每条新消息的回调（先回调再 append）。
                        None → 使用默认 messages.append。
                        主 Agent 由 SessionController 注入；
                        SubAgent 永远不传入（使用默认 append）。

        循环体会原地修改 messages（通过 on_message 或默认 append）。
        """
        for _ in range(self.max_steps):
            # Hook: 每轮 LLM 调用前，允许 hooks 注入额外消息（todo 提醒）
            inject = self._trigger_hook("PreLLMCall")
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
                # 归一化 SDK message → dict，然后通过回调出口
                assistant_msg = normalize_message(msg)
                _emit_message(assistant_msg, messages, on_message)
                self._execute_tool_calls(msg.tool_calls, messages, on_message)

            # Hook: 每轮结束后通知 hooks（更新todo记录）
            tool_calls = msg.tool_calls if stop_reason == "tool_calls" else None
            self._trigger_hook("PostRound", stop_reason, tool_calls)

            if stop_reason != "tool_calls":
                # 持久化 final assistant（S1 修复后的 _emit_message 保证只追加一次）
                # S2 尚未执行 S5，此处先复用现有私有归一化函数。
                final_msg = normalize_message(msg)
                _emit_message(final_msg, messages, on_message)
                self._trigger_hook("PreAgentStop", messages)
                return msg.content or "（模型未返回文本）"

        self._trigger_hook("PreAgentStop", messages)
        return "Agent 已停止：达到最大步数限制。"

    def _trigger_hook(self, event: str, *args) -> dict | None:
        """触发当前 Agent 参与的全局扩展点。"""
        return trigger_hooks(event, *args)

    def _execute_tool_calls(
        self,
        tool_calls,
        messages: list,
        on_message: Callable[[dict], None] | None = None,
    ) -> None:
        """执行 msg 中的所有 tool_calls，结果以 role='tool' 经回调追加到 messages。"""
        for tc in tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            self.print_handler(name, args)

            result = self.executor.execute(name, args)

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
            _emit_message(tool_msg, messages, on_message)


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

    def _execute_tool_calls(
        self,
        tool_calls,
        messages: list,
        on_message: Callable[[dict], None] | None = None,
    ) -> None:
        """覆盖父类：跟踪轮数，第 30 轮时注入提醒。

        SubAgent 永远不接收主 SessionController 的 on_message 回调——
        其 system、user、assistant 和 tool 消息只存在于本次子任务的局部列表。
        """
        self._round += 1
        if self._round == 30:
            reminder = {
                "role": "user",
                "content": "你已达到最大轮数限制，请基于已有信息给出当前最佳结论。",
            }
            _emit_message(reminder, messages, on_message)
        super()._execute_tool_calls(tool_calls, messages, on_message)
