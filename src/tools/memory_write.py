"""Tool entry point for explicit durable Memory writes."""

from memory.models import MemoryChange, MemoryType
from memory.service import MemoryService
from tooling.base import Tool, ToolParameter


class MemoryWriteTool(Tool):
    """Add a new Memory or fully replace one existing Memory."""

    def __init__(self, memory_service: MemoryService):
        super().__init__(
            name="memory_write",
            description=(
                "保存跨会话长期有效的用户偏好、反馈、项目事实或参考入口。"
                "仅支持显式 add 或完整 update；不确定是否长期有效时不要调用。"
            ),
        )
        self._memory_service = memory_service

    def get_parameters(self) -> list[ToolParameter]:
        """Return the function-calling schema for one Memory change."""
        return [
            ToolParameter(
                "action",
                "string",
                "新增使用 add，完整替换同名记录使用 update。",
                enum=["add", "update"],
            ),
            ToolParameter(
                "name",
                "string",
                "稳定且不可更改的 lowercase ASCII kebab-case 名称。",
            ),
            ToolParameter(
                "memory_type",
                "string",
                "长期记忆类别。",
                enum=["user", "feedback", "project", "reference"],
            ),
            ToolParameter(
                "description",
                "string",
                "用于检索和人工理解的一行描述。",
            ),
            ToolParameter(
                "body",
                "string",
                "完整、自洽、长期有效的记忆正文。",
            ),
        ]

    def run(self, parameters: dict) -> dict:
        """Validate tool input and apply one Memory change."""
        if not isinstance(parameters, dict):
            return {"error": "parameters must be an object"}

        required = {"action", "name", "memory_type", "description", "body"}
        missing = sorted(required - parameters.keys())
        if missing:
            return {"error": f"missing required parameters: {', '.join(missing)}"}

        name = parameters.get("name")
        try:
            change = MemoryChange(
                action=parameters["action"],
                name=name,
                memory_type=MemoryType(parameters["memory_type"]),
                description=parameters["description"],
                body=parameters["body"],
            )
            record = self._memory_service.apply_change(change)
        except Exception as exc:
            message = exc.args[0] if exc.args else str(exc)
            return {"error": str(message), "name": name}

        result = "memory_added" if change.action == "add" else "memory_updated"
        return {"result": result, "name": record.name}
