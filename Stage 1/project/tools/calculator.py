"""计算器工具 —— 安全计算数学表达式。"""

from tool import Tool


def _calc(expression: str) -> dict:
    ans = eval(expression, {"__builtins__": {}}, {})
    return {"expression": expression, "result": ans}


tool_calculator = Tool(
    name="calculator",
    description="计算数学表达式，支持 + - * / 和括号。",
    parameters={
        "expression": 
            {"type": "string", 
            "description": "如 '3 + 4 * 2'"
            }
        },
    required=("expression",),
    fn=_calc,
)
