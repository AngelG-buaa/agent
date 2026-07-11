"""TodoWrite 工具 —— 让 Agent 在执行复杂任务前规划步骤、执行中跟踪进度。

设计参考: Claude Code TodoWrite V1 + learn-claude-code s05 教程。

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

    def __init__(self):
        super().__init__(
            name="todo_write",
            description=(
                "Use this tool to create and manage a structured task list "
                "for your current coding session. This helps you track progress, "
                "organize complex tasks, and demonstrate thoroughness. "
                "This tool does not perform any actual work — it only manages "
                "the task list."
            ),
        )

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

    @staticmethod
    def _print_todos() -> None:
        """在终端打印当前任务列表，带状态图标。"""
        lines = ["\n## Current Tasks"]
        if not CURRENT_TODOS:
            lines.append("  (no tasks)")
        else:
            for t in CURRENT_TODOS:
                icon = STATUS_ICONS.get(t.get("status", "pending"), " ")
                lines.append(f"  [{icon}] {t.get('content', '')}")
        print("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
# Hook 注册：TodoWrite 提醒机制
# ═══════════════════════════════════════════════════════════════


def _create_todo_reminder_hooks():
    """Closure 工厂：捕获独立计数器，避免 Agent 实例间状态泄漏。

    返回 (on_pre_llm_call, on_post_round) 两个回调。
    每次 register_todo_hooks() 调用创建新的闭包，计数器相互独立。
    """
    rounds_since_todo = 0

    def on_post_round(stop_reason, tool_calls):
        nonlocal rounds_since_todo
        if tool_calls and any(
            tc.function.name == "todo_write" for tc in tool_calls
        ):
            rounds_since_todo = 0
        else:
            rounds_since_todo += 1
        return None  # 纯副作用，不阻断

    def on_pre_llm_call():
        nonlocal rounds_since_todo
        if rounds_since_todo >= 3:
            rounds_since_todo = 0
            return {
                "messages": [{
                    "role": "user",
                    "content": "<reminder>Update your todos.</reminder>",
                }]
            }
        return None  # 无需注入

    return on_pre_llm_call, on_post_round


def register_todo_hooks():
    """装配 TodoWrite 提醒 hooks。

    在 Agent 启动前调用一次。向 PreLLMCall 和 PostRound 注册回调：
      - PostRound: 跟踪连续未调用 todo_write 的轮数
      - PreLLMCall: 连续 3 轮后注入提醒消息

    用法（main.py 中，在 register_all() 之后、agent.run() 之前）:
        from tools.todo_write import register_todo_hooks
        register_todo_hooks()
    """
    from hooks import register_hook
    on_pre_llm, on_post = _create_todo_reminder_hooks()
    register_hook("PreLLMCall", on_pre_llm)
    register_hook("PostRound", on_post)
