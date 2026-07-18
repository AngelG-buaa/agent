"""TodoWrite 工具 —— 让 Agent 在执行复杂任务前规划步骤、执行中跟踪进度。

职责:
  - 维护全局任务列表（进程内存，不持久化）
  - 校验输入合法性（status 枚举、content 非空）
  - 终端可视化输出任务进度

不负责:
  - 计数器维护（在 Agent 循环中）
  - 提醒注入（在 Agent 循环中）
  - 任何实际工作（这不是一个执行工具）
"""

from tooling.base import Tool, ToolParameter
from terminal.io import OutputWriter, TerminalOutputWriter

# 全局任务列表，进程内存存储，每次 todo_write 调用整体替换
CURRENT_TODOS: list[dict] = []

# 合法状态枚举
VALID_STATUSES = {"pending", "in_progress", "completed"}

# 状态 → 图标映射
STATUS_ICONS = {
    "pending": " ",
    "in_progress": "▸",
    "completed": "✓",
}


class TodoWriteTool(Tool):
    """规划工具：创建和管理当前会话的结构化任务列表。

    此工具不执行任何实际工作（不能读文件、不能运行命令）。
    它的唯一目的是让 Agent 在执行复杂任务前组织思路、跟踪进度。
    """

    def __init__(self, output: OutputWriter | None = None):
        super().__init__(
            name="todo_write",
            description=(
            "为当前编码会话创建和管理结构化任务列表，帮助跟踪进度、组织复杂任务。此工具仅维护列表，不执行实际操作。"
            ),
        )
        self._output = output or TerminalOutputWriter()

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="todos",
                type="array",
                description=(
                    "完整的任务列表。每个任务包含以下字段：\n"
                    "- content（字符串）：任务的描述内容，不能为空。\n"
                    "- status（字符串）：任务的状态，必须是 'pending'、'in_progress' 或 'completed' 之一。\n"
                    "示例：\n"
                    '[{"content": "分析需求", "status": "completed"}, '
                    '{"content": "编写代码", "status": "in_progress"}, '
                    '{"content": "测试功能", "status": "pending"}]'
                ),
                required=True,
            ),
        ]

    def run(self, parameters: dict) -> dict:
        """验证 todos 参数，更新全局列表，打印可视化输出。"""
        global CURRENT_TODOS
        todos = parameters.get("todos")

        # 校验每个 todo item
        for i, todo in enumerate(todos):
            content = todo.get("content", "")
            status = todo.get("status", "")

            if not isinstance(content, str) or not content.strip():
                return {"error": f"Task at index {i}: content cannot be empty"}

            if status not in VALID_STATUSES:
                return {
                    "error": (
                        f"Invalid status: '{status}' at index {i}. "
                        f"Must be one of: {', '.join(sorted(VALID_STATUSES))}"
                    )
                }

        # 更新全局列表（用 clear + extend 保持同一引用，便于测试和外部访问）
        CURRENT_TODOS.clear()
        CURRENT_TODOS.extend(todos)

        # 终端可视化输出
        self._print_todos()

        return {"result": f"Updated {len(CURRENT_TODOS)} tasks"}

    def _print_todos(self) -> None:
        """在终端打印当前任务列表，带状态图标。"""
        lines = ["\n## Current Tasks"]
        if not CURRENT_TODOS:
            lines.append("  (no tasks)")
        else:
            for t in CURRENT_TODOS:
                icon = STATUS_ICONS.get(t.get("status", "pending"), " ")
                lines.append(f"  [{icon}] {t.get('content', '')}")
        self._output.info("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
# Session 持久化：快照与替换
# ═══════════════════════════════════════════════════════════════


def snapshot_todos() -> list[dict]:
    """返回 CURRENT_TODOS 的浅拷贝快照（用于持久化）。"""
    return list(CURRENT_TODOS)


def replace_todos(todos: list[dict]) -> None:
    """原子替换 Todo 列表（clear + extend，不重新绑定引用）。

    空列表也必须替换——覆盖旧 session 的 Todo 状态。
    """
    CURRENT_TODOS.clear()
    CURRENT_TODOS.extend(todos)


# ═══════════════════════════════════════════════════════════════
# Hook 注册：TodoWrite 提醒机制
# ═══════════════════════════════════════════════════════════════


class TodoReminderHandle:
    """封装 reminder 计数器。不直接访问全局 HOOKS。

    用法:
        handle = register_todo_hooks()
        handle.reset()   # 新建/恢复/切换 session 后
        handle.dispose() # Controller 关闭时
    """

    def __init__(self, pre_disposer, post_disposer):
        self._pre_disposer = pre_disposer
        self._post_disposer = post_disposer
        self._counter = 0
        self._disposed = False

    def increment_and_check(self) -> bool:
        """递增计数器，返回是否应触发提醒。"""
        self._counter += 1
        return self._counter >= 3

    def reset(self) -> None:
        """重置计数器为零（幂等）。"""
        self._counter = 0

    def dispose(self) -> None:
        """调用 disposer 注销 hooks（幂等）。"""
        if self._disposed:
            return
        self._pre_disposer()
        self._post_disposer()
        self._disposed = True


def register_todo_hooks() -> TodoReminderHandle:
    """装配 TodoWrite 提醒 hooks 并返回 handle。

    用法:
        todo_handle = register_todo_hooks()
    """
    from hooks import register_hook

    handle = TodoReminderHandle(None, None)

    def on_post_round(stop_reason, tool_calls):
        if tool_calls and any(
            tc.function.name == "todo_write" for tc in tool_calls
        ):
            handle.reset()
        elif CURRENT_TODOS:
            handle.increment_and_check()
        return None

    def on_pre_llm_call():
        if handle._counter >= 3:
            handle.reset()
            return {
                "messages": [{
                    "role": "user",
                    "content": "<reminder>Update your todos.</reminder>",
                }]
            }
        return None

    pre_disposer = register_hook("PreLLMCall", on_pre_llm_call)
    post_disposer = register_hook("PostRound", on_post_round)

    handle._pre_disposer = pre_disposer
    handle._post_disposer = post_disposer

    return handle
