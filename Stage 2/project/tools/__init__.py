"""内置工具集 —— 统一注册入口。"""

from pathlib import Path

from tooling.executor import ToolExecutor
from tools.calculator import CalculatorTool
from tools.get_time import GetTimeTool
from tools.read_chunk import ReadChunkTool
from tools.read_file import ReadFileTool
from tools.search_knowledge import SearchKnowledgeTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool
from tools.bash import BashTool
from tools.write_file import WriteFileTool
from tools.edit_file import EditFileTool


def register_all(
    executor: ToolExecutor,
    include_dangerous: bool = True,
    workdir: str | Path | None = None,
) -> None:
    """将所有内置工具注册到 executor。

    Args:
        executor: 目标 ToolExecutor
        include_dangerous: 是否注册 destructive/sensitive 工具
        workdir: 工作区根目录（bash 的执行目录、文件工具的路径基准）
    """
    executor.register(CalculatorTool())
    executor.register(GetTimeTool())
    executor.register(ReadChunkTool())
    executor.register(ReadFileTool(base_dir=workdir))
    executor.register(SearchKnowledgeTool())
    executor.register(WebFetchTool())
    executor.register(WebSearchTool())

    if include_dangerous:
        executor.register(BashTool(workdir=workdir))
        executor.register(WriteFileTool(base_dir=workdir))
        executor.register(EditFileTool(base_dir=workdir))
