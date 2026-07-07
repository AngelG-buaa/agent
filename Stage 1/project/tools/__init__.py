"""内置工具集 —— 统一注册入口。"""

from tool_registry import ToolRegistry
from tools.calculator import tool_calculator
from tools.get_time import tool_get_time


def register_all(registry: ToolRegistry) -> None:
    """将所有内置工具注册到给定 registry。"""
    registry.register(tool_calculator)
    registry.register(tool_get_time)
