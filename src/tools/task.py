"""Task 工具 —— 让 Agent 将复杂子任务委派给 Sub-Agent 独立执行。

核心机制:
  - task 工具接收自然语言描述，创建 SubAgent 实例
  - SubAgent 拥有独立的上下文（新鲜 messages[]），中间步骤不污染主 Agent
  - SubAgent 仅返回最终文本结论，中间过程随函数返回而丢弃
"""

from tooling.base import Tool, ToolParameter


class TaskTool(Tool):
    """委派工具：启动 Sub-Agent 执行独立子任务。

    LLM 应在以下场景使用此工具：
      - 子任务可独立完成，不依赖主 Agent 的中间状态
      - 子任务需要多步骤（搜索、读取、汇总），会产生大量中间上下文
      - 主 Agent 需要"干净的结论"而非原始数据

    用法（LLM 视角）:
        task(description="搜索项目中所有使用 async/await 的文件并汇总其用途")
    """

    def __init__(self, llm, executor):
        super().__init__(
            name="task",
            description=(
            "启动一个子Agent，独立完成复杂多步任务。它拥有独立上下文，自主调用工具，仅返回最终结论。"
            ),
        )
        self._llm = llm
        self._executor = executor

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="description",
                type="string",
                description=(
                    "A short (3-5 word) description of the task to delegate "
                    "to the sub-agent. Be specific about what the sub-agent "
                    "should accomplish and what information to return."
                ),
                required=True,
            ),
        ]

    def run(self, parameters: dict) -> dict:
        """执行委派：验证输入 → 启动 SubAgent → 返回结论。"""
        description = parameters.get("description", "").strip()
        if not description:
            return {"error": "description is required"}

        try:
            result = spawn_subagent(description, self._llm, self._executor)
            return {"result": result}
        except Exception as exc:
            return {"error": str(exc)}


def spawn_subagent(description: str, llm, executor) -> str:
    """创建 SubAgent，同步执行子任务，仅返回最终文本结论。

    这是 task 工具的实际执行逻辑。与 TaskTool 分离是为了便于
    测试（可独立调用，无需构造完整的 Tool 调用链）。

    Args:
        description: 子任务的自然语言描述
        llm: LLMClient 实例（与主 Agent 共享）
        executor: ToolExecutor 实例（与主 Agent 共享，权限 session 自然共享）

    Returns:
        SubAgent 的最终文本回复
    """
    from agent.agent import SubAgent

    print(f"\n[Subagent spawned] {description[:100]}")

    sub = SubAgent(llm=llm, executor=executor)
    messages = [
        {"role": "system", "content": sub.system_prompt},
        {"role": "user", "content": description},
    ]
    result = sub.run(messages)

    print(f"[Subagent done]")
    return result
