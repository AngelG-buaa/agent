"""内置工具集 —— 统一注册入口。"""

from pathlib import Path

from tooling.executor import ToolExecutor
from tools.calculator import CalculatorTool
from tools.get_time import GetTimeTool
from tools.memory_write import MemoryWriteTool
from tools.read_chunk import ReadChunkTool
from tools.read_file import ReadFileTool
from tools.search_knowledge import SearchKnowledgeTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool
from tools.bash import BashTool
from tools.write_file import WriteFileTool
from tools.edit_file import EditFileTool
from tools.todo_write import TodoWriteTool
from tools.task import TaskTool
from tools.ask_user import AskUserTool


def register_all(
    executor: ToolExecutor,
    include_dangerous: bool = True,
    workdir: str | Path | None = None,
    llm=None,
    memory_service=None,
) -> None:
    """将所有内置工具注册到 executor。

    Args:
        executor: 目标 ToolExecutor
        include_dangerous: 是否注册 destructive/sensitive 工具
        workdir: 工作区根目录（bash 的执行目录、文件工具的路径基准）
        llm: LLMClient 实例（TaskTool 需要，可选）
        memory_service: MemoryService 实例（MemoryWriteTool 需要，可选）
    """
    executor.register(TodoWriteTool())
    executor.register(TaskTool(llm=llm, executor=executor))
    executor.register(AskUserTool())
    executor.register(CalculatorTool())
    executor.register(GetTimeTool())
    executor.register(ReadChunkTool())
    executor.register(ReadFileTool(base_dir=workdir))
    executor.register(SearchKnowledgeTool())
    executor.register(WebFetchTool())
    executor.register(WebSearchTool())

    if memory_service is not None:
        executor.register(MemoryWriteTool(memory_service))

    if include_dangerous:
        executor.register(BashTool(workdir=workdir))
        executor.register(WriteFileTool(base_dir=workdir))
        executor.register(EditFileTool(base_dir=workdir))
