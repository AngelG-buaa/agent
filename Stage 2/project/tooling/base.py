"""Tool 基类 —— 所有工具的抽象父类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RiskLevel(Enum):
    """工具风险等级。元数据标记，不参与权限判断。"""
    SAFE = "safe"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


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

    def __init__(self, name: str, description: str, risk_level: RiskLevel = RiskLevel.SAFE):
        self.name = name
        self.description = description
        self.risk_level = risk_level  # 元数据标记，不参与权限判断

    # ---- 权限管线方法 ----

    def permission_target(self, params: dict) -> str:
        """返回规则内容匹配的目标字符串。

        默认返回空串（不参与内容匹配）。
        bash 覆盖返回 command；文件工具覆盖返回 resolved path。
        """
        return ""

    # ------------------------------------------------------------------------

    @staticmethod
    def resolve_path(path_str: str, base_dir: str | Path) -> Path:
        """将相对路径解析为绝对路径。

        纯路径工具方法，不做安全边界判断（安全边界由 PermissionEngine 负责）。
        供 write_file / edit_file 等文件工具共用。

        Args:
            path_str: 相对路径
            base_dir: 基准目录

        Raises:
            ValueError: 路径无法解析
        """
        return (Path(base_dir) / path_str).resolve()

    # ---- 抽象方法 ----

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
