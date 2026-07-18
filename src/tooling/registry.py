"""工具注册表 —— 按名注册、导出 schema、分发执行。"""

from tooling.base import Tool


class ToolRegistry:
    """管理工具集合。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具实例（同名覆盖）。"""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool | None:
        """按名获取工具实例。"""
        return self._tools.get(name)

    def get_schemas(self) -> list[dict]:
        """导出所有工具的 API schema。"""
        return [t.to_schema() for t in self._tools.values()]

