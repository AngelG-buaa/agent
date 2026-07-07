"""工具注册表 —— 注册 + 导出 schema + 按名分发执行。"""

from tool import Tool


class ToolRegistry:
    """管理工具集合。参考 Claude Code 的扁平注册 + 按名分发模式。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具（同名会覆盖）。"""
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict]:
        """导出所有工具的 API schema，直接喂给 LLM。"""
        return [t.to_tools_schema() for t in self._tools.values()]

    def execute(self, name: str, args: dict) -> dict:
        """按名称分发执行；失败时返回 {"error": ...} 而非抛异常。"""
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"未知工具: {name}"}
        if tool.fn is None:
            return {"error": f"工具 '{name}' 未绑定执行函数"}
        try:
            # ** 把字典的 key 变成参数名、value 变成参数值
            return tool.fn(**args)
        except Exception as exc:
            return {"error": str(exc)}
