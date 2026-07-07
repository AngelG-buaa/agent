"""Tool 数据类 —— 单个工具的 schema + 执行函数。"""

from dataclasses import dataclass


@dataclass
class Tool:
    """一个可被 Agent 调用的工具。"""
    name: str
    description: str
    parameters: dict             # JSON Schema properties
    # 注意：默认值使用 tuple() 而不是 list[] 
    # 因为 tuple 不可变，避免多个实例共享同一个可变列表带来的副作用
    required: tuple = ()
    fn: callable | None = None   # 实际执行函数,callable 是 Python 的类型标注，表示"任何可以被调用的东西"
                                 # 函数、lambda、实现了 __call__ 的类的实例都算

    def to_tools_schema(self) -> dict:
        """转为 OpenAI / DeepSeek function 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": list(self.required),
                },
            },
        }
