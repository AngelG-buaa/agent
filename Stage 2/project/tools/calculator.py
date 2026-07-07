"""计算器工具。"""

from tool import Tool, ToolParameter


class CalculatorTool(Tool):
    def __init__(self):
        super().__init__(
            name="calculator",
            description="计算数学表达式，支持 + - * / 和括号。",
        )

    def get_parameters(self):
        return [
            ToolParameter("expression", "string", "如 '3 + 4 * 2'"),
        ]

    def run(self, params):
        expression = params["expression"]
        ans = eval(expression, {"__builtins__": {}}, {})
        return {"expression": expression, "result": ans}
