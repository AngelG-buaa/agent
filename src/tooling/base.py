"""Tool 基类 —— 所有工具的抽象父类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolParameter:
    """工具参数的描述。"""

    name: str
    type: str          # JSON Schema type: "string" | "number" | "boolean"
    description: str
    required: bool = True
    enum: list[str] | None = None

    def to_property(self) -> dict:
        prop: dict = {
                "type": self.type,
                "description": self.description
            }
        if self.enum is not None:
            prop["enum"] = self.enum

        return prop


class Tool(ABC):
    """工具基类。每个具体工具继承此类，实现 run() 和 get_parameters()。

    用法：
        class MyTool(Tool):
            def __init__(self):
                super().__init__(name="my_tool", description="...")

            def get_parameters(self):
                return [ToolParameter("x", "string", "...")]

            def run(self, params):
                return {"result": params["x"]}
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    def run(self, parameters: dict) -> dict:
        """执行工具，返回结果 dict。"""
        ...

    @abstractmethod
    def get_parameters(self) -> list[ToolParameter]:
        """返回参数定义列表。"""
        ...

    def to_schema(self) -> dict:
        """转为 OpenAI function calling 格式。"""
        properties = {}
        required: list[str] = []
        for p in self.get_parameters():
            prop = p.to_property()
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
