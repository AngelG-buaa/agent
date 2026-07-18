"""AskUser 工具 —— 让 Agent 在执行过程中向用户提问并获取回答。

核心机制:
  - Agent 调用 ask_user(question="...") → 阻塞等待用户输入
  - 用户回答后以 tool result 形式注入对话
  - 检测无效回答（"不知道"/"随便"/"你自己决定"等）标记 is_valid=False
"""

from tooling.base import Tool, ToolParameter

# 无效回答关键词（不区分大小写，子串匹配）
_INVALID_PATTERNS = [
    "不知道", "随便", "都行", "你自己决定", "你看着办",
    "无所谓", "都可以", "dont know", "i don't know",
    "whatever", "up to you", "随你", "看你",
]


class AskUserTool(Tool):
    """反问工具：Agent 在执行过程中向用户提问并获取回答。

    LLM 应在以下场景使用：
      - 用户指令有歧义，多种理解均合理
      - 任务需要用户偏好/选择（格式、风格、优先级）
      - 关键信息缺失且用户最可能知道答案

    不应使用的场景：
      - 可通过搜索/文件读取等工具获取的事实信息
      - 微小的格式偏好（用合理默认值即可）
      - 已经问过且用户给出有效回答的问题
    """

    def __init__(self):
        super().__init__(
            name="ask_user",
            description=(
                "向用户提问以澄清歧义或获取偏好。仅在无法通过其他工具获取信息、"
                "且用户输入无法确定意图时使用。同一问题不应重复询问。"
                "问题应包含足够的背景信息（当前在做什么、为什么需要用户输入），"
                "使用户无需查看对话历史即可理解并回答。"
                "如果需要反问用户，不要在同一轮响应中混入其他工具调用。"
            ),
        )

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="question",
                type="string",
                description=(
                    "向用户提出的问题。应包含清晰的背景说明和可选选项（如适用）。"
                    "例如：'当前有 data.json 和 data.csv 两个文件，你想分析哪一个？'"
                ),
                required=True,
            ),
        ]

    def run(self, parameters: dict) -> dict:
        """阻塞等待用户输入，返回答案及有效性标记。"""
        question = parameters.get("question", "").strip()
        if not question:
            return {"error": "question 参数不能为空"}

        print(f"\n  ❓ Agent 提问: {question}")
        try:
            answer = input("  👤 你的回答: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return {"answer": "用户未回答（中断）", "is_valid": False}

        if not answer:
            return {"answer": "用户未提供回答", "is_valid": False}

        is_valid = not any(p in answer.lower() for p in _INVALID_PATTERNS)
        return {"answer": answer, "is_valid": is_valid}
