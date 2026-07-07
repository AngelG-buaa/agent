"""内置工具集 —— 统一注册入口。"""

from tool_registry import ToolRegistry
from tools.calculator import CalculatorTool
from tools.get_time import GetTimeTool
from tools.read_chunk import ReadChunkTool
from tools.search_knowledge import SearchKnowledgeTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool
from tools.bash import BashTool
from tools.write_file import WriteFileTool
from tools.edit_file import EditFileTool


def register_all(registry: ToolRegistry, include_dangerous: bool = True) -> None:
    """将所有内置工具注册到 registry。

    Args:
        registry: 目标 ToolRegistry
        include_dangerous: 是否注册 destructive/sensitive 工具（bash, write_file, edit_file）。
                           用于 index_cli 等不需要危险工具的场景。
    """
    registry.register(CalculatorTool())
    registry.register(GetTimeTool())
    registry.register(ReadChunkTool())
    registry.register(SearchKnowledgeTool())
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())

    if include_dangerous:
        registry.register(BashTool())
        registry.register(WriteFileTool())
        registry.register(EditFileTool())
