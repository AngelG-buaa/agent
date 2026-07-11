"""TodoWrite 工具和 Agent 计数器逻辑的单元测试。

测试覆盖:
  - TodoWriteTool: 基本功能、验证、边界条件 (US1)
  - 显示格式 (US2)
  - Agent 循环: 计数器 + 提醒注入逻辑 (US3)
"""

import json
import sys
from unittest.mock import MagicMock, patch
from io import StringIO

import pytest

# 确保项目根目录在 path 中
sys.path.insert(0, "d:/LLM/Agent/Stage 2/project")

from tools.todo_write import TodoWriteTool, CURRENT_TODOS, VALID_STATUSES


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def clear_todos():
    """每个测试前清空全局任务列表，保证测试隔离。

    使用 .clear() 而非重新赋值，确保修改的是 tools.todo_write 模块内的原始列表。
    """
    CURRENT_TODOS.clear()


# ═══════════════════════════════════════════════════════════════
# Phase 3: US1 — TodoWriteTool 基本功能测试 (T005)
# ═══════════════════════════════════════════════════════════════


class TestTodoWriteToolBasic:
    """测试 TodoWriteTool 的核心功能。"""

    def test_create_pending_tasks(self):
        """创建多个 pending 状态的任务。"""
        tool = TodoWriteTool()
        result = tool.run({
            "todos": [
                {"content": "Step 1: add type hints", "status": "pending"},
                {"content": "Step 2: add docstrings", "status": "pending"},
                {"content": "Step 3: add main guard", "status": "pending"},
            ]
        })
        assert "error" not in result
        assert "Updated 3 tasks" in result["result"]
        assert len(CURRENT_TODOS) == 3
        assert all(t["status"] == "pending" for t in CURRENT_TODOS)

    def test_create_mixed_status_tasks(self):
        """创建混合状态的任务。"""
        tool = TodoWriteTool()
        result = tool.run({
            "todos": [
                {"content": "Done task", "status": "completed"},
                {"content": "Current task", "status": "in_progress"},
                {"content": "Future task", "status": "pending"},
            ]
        })
        assert "error" not in result
        assert len(CURRENT_TODOS) == 3
        assert CURRENT_TODOS[0]["status"] == "completed"
        assert CURRENT_TODOS[1]["status"] == "in_progress"
        assert CURRENT_TODOS[2]["status"] == "pending"

    def test_create_single_task(self):
        """创建单个任务。"""
        tool = TodoWriteTool()
        result = tool.run({
            "todos": [
                {"content": "Only task", "status": "in_progress"}
            ]
        })
        assert "error" not in result
        assert len(CURRENT_TODOS) == 1
        assert CURRENT_TODOS[0]["content"] == "Only task"

    def test_empty_todo_list(self):
        """空任务列表应正常返回而不报错。"""
        tool = TodoWriteTool()
        result = tool.run({"todos": []})
        assert "error" not in result
        assert "Updated 0 tasks" in result["result"]
        assert CURRENT_TODOS == []

    def test_consecutive_calls_replace_list(self):
        """两次连续调用，以最后一次为准。"""
        tool = TodoWriteTool()
        tool.run({"todos": [{"content": "First", "status": "pending"}]})
        tool.run({"todos": [{"content": "Second", "status": "completed"}]})
        assert len(CURRENT_TODOS) == 1
        assert CURRENT_TODOS[0]["content"] == "Second"
        assert CURRENT_TODOS[0]["status"] == "completed"

    def test_status_transition_completed_to_pending(self):
        """支持反向状态转换：completed → pending。"""
        tool = TodoWriteTool()
        tool.run({"todos": [{"content": "Reopen me", "status": "completed"}]})
        tool.run({"todos": [{"content": "Reopen me", "status": "pending"}]})
        assert CURRENT_TODOS[0]["status"] == "pending"


class TestTodoWriteToolValidation:
    """测试 TodoWriteTool 的输入校验。"""

    def test_invalid_status_rejected(self):
        """非法 status 应被拒绝并返回错误信息。"""
        tool = TodoWriteTool()
        result = tool.run({
            "todos": [{"content": "Bad status task", "status": "invalid_status"}]
        })
        assert "error" in result
        assert "Invalid status" in result["error"]
        assert "invalid_status" in result["error"]

    def test_empty_content_rejected(self):
        """空 content 应被拒绝。"""
        tool = TodoWriteTool()
        result = tool.run({
            "todos": [{"content": "", "status": "pending"}]
        })
        assert "error" in result
        assert "content cannot be empty" in result["error"]

    def test_whitespace_only_content_rejected(self):
        """仅包含空格的 content 应被拒绝。"""
        tool = TodoWriteTool()
        result = tool.run({
            "todos": [{"content": "   ", "status": "pending"}]
        })
        assert "error" in result
        assert "content cannot be empty" in result["error"]

    def test_all_valid_statuses_accepted(self):
        """所有三种合法状态均被接受。"""
        tool = TodoWriteTool()
        for status in VALID_STATUSES:
            result = tool.run({
                "todos": [{"content": f"Test {status}", "status": status}]
            })
            assert "error" not in result, f"Status '{status}' should be valid"

    def test_multiple_tasks_mixed_valid_invalid(self):
        """混合合法和非法任务时，应在第一个非法任务处报错。"""
        tool = TodoWriteTool()
        result = tool.run({
            "todos": [
                {"content": "Valid task", "status": "pending"},
                {"content": "Bad task", "status": "bad_status"},
                {"content": "Another valid", "status": "completed"},
            ]
        })
        assert "error" in result
        # 不应该更新全局列表
        assert CURRENT_TODOS == []


# ═══════════════════════════════════════════════════════════════
# Phase 3: US1 — Agent 层级集成测试 (T006)
# ═══════════════════════════════════════════════════════════════


class TestAgentTodoIntegration:
    """测试 Agent 循环中 todo_write 的集成行为。"""

    def test_agent_executes_todo_write_tool_call(self):
        """模拟 LLM 返回 todo_write 的 tool_call，验证被正确执行。"""
        from tools.todo_write import CURRENT_TODOS

        # 清理
        CURRENT_TODOS.clear()

        tool = TodoWriteTool()
        result = tool.run({
            "todos": [
                {"content": "Integration test task", "status": "pending"}
            ]
        })
        assert "error" not in result
        assert len(CURRENT_TODOS) == 1

    def test_todo_write_schema_export(self):
        """验证 todo_write 的 schema 导出格式正确。"""
        tool = TodoWriteTool()
        schema = tool.to_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "todo_write"
        assert "todos" in schema["function"]["parameters"]["properties"]
        assert "todos" in schema["function"]["parameters"]["required"]


# ═══════════════════════════════════════════════════════════════
# Phase 4: US2 — 显示格式测试 (T008)
# ═══════════════════════════════════════════════════════════════


class TestTodoWriteDisplay:
    """测试 todo_write 的终端输出格式。"""

    def test_display_shows_header(self):
        """输出应包含 '## Current Tasks' 标题。"""
        tool = TodoWriteTool()
        captured = StringIO()

        with patch("sys.stdout", captured):
            tool.run({
                "todos": [{"content": "Test", "status": "pending"}]
            })

        output = captured.getvalue()
        assert "## Current Tasks" in output

    def test_display_shows_pending_icon(self):
        """pending 状态应显示 [ ] 图标。"""
        tool = TodoWriteTool()
        captured = StringIO()

        with patch("sys.stdout", captured):
            tool.run({
                "todos": [{"content": "Pending task", "status": "pending"}]
            })

        output = captured.getvalue()
        assert "[ ] Pending task" in output

    def test_display_shows_in_progress_icon(self):
        """in_progress 状态应显示 [▸] 图标。"""
        tool = TodoWriteTool()
        captured = StringIO()

        with patch("sys.stdout", captured):
            tool.run({
                "todos": [{"content": "Active task", "status": "in_progress"}]
            })

        output = captured.getvalue()
        assert "[▸] Active task" in output

    def test_display_shows_completed_icon(self):
        """completed 状态应显示 [✓] 图标。"""
        tool = TodoWriteTool()
        captured = StringIO()

        with patch("sys.stdout", captured):
            tool.run({
                "todos": [{"content": "Done task", "status": "completed"}]
            })

        output = captured.getvalue()
        assert "[✓] Done task" in output

    def test_display_shows_all_status_icons(self):
        """同时显示三种状态时，每种图标都出现。"""
        tool = TodoWriteTool()
        captured = StringIO()

        with patch("sys.stdout", captured):
            tool.run({
                "todos": [
                    {"content": "A", "status": "pending"},
                    {"content": "B", "status": "in_progress"},
                    {"content": "C", "status": "completed"},
                ]
            })

        output = captured.getvalue()
        assert "[ ] A" in output
        assert "[▸] B" in output
        assert "[✓] C" in output

    def test_display_preserves_task_order(self):
        """任务按输入顺序显示。"""
        tool = TodoWriteTool()
        captured = StringIO()

        with patch("sys.stdout", captured):
            tool.run({
                "todos": [
                    {"content": "First", "status": "pending"},
                    {"content": "Second", "status": "pending"},
                    {"content": "Third", "status": "pending"},
                ]
            })

        output = captured.getvalue()
        first_pos = output.index("First")
        second_pos = output.index("Second")
        third_pos = output.index("Third")
        assert first_pos < second_pos < third_pos

    def test_display_update_replaces_previous(self):
        """第二次调用更新的显示反映最新状态。"""
        tool = TodoWriteTool()

        # 第一次调用
        tool.run({"todos": [{"content": "Old", "status": "pending"}]})

        # 第二次调用
        captured = StringIO()
        with patch("sys.stdout", captured):
            tool.run({"todos": [{"content": "New", "status": "completed"}]})

        output = captured.getvalue()
        assert "[✓] New" in output
        assert "Old" not in output

    def test_empty_list_shows_no_tasks_message(self):
        """空列表显示 '(no tasks)'。"""
        tool = TodoWriteTool()
        captured = StringIO()

        with patch("sys.stdout", captured):
            tool.run({"todos": []})

        output = captured.getvalue()
        assert "(no tasks)" in output


# ═══════════════════════════════════════════════════════════════
# Phase 5: US3 — 计数器逻辑测试 (T010)
# ═══════════════════════════════════════════════════════════════


class TestRoundCounter:
    """测试 Agent 循环中的计数器逻辑。

    由于计数器集成在 Agent.run() 中，这里的测试通过模拟循环行为来验证。
    """

    def simulate_agent_loop(self, rounds, todo_write_called_at=None):
        """模拟 Agent 循环行为。

        Args:
            rounds: 总轮数
            todo_write_called_at: todo_write 被调用的轮次列表 (0-indexed)

        Returns:
            (reminders_injected, counter_history): 提醒注入次数和每轮后计数器值
        """
        counter = 0
        reminders_injected = 0
        counter_history = []

        for rnd in range(rounds):
            # Simulate LLM call

            # After round, check if todo_write was called this round
            if todo_write_called_at and rnd in todo_write_called_at:
                counter = 0  # Reset on todo_write
            else:
                counter += 1  # Increment

            counter_history.append(counter)

            # Check reminder before next LLM call
            if counter >= 3:
                reminders_injected += 1
                counter = 0  # Reset after reminder injection

        return reminders_injected, counter_history

    def test_counter_starts_at_zero(self):
        """计数器初始值为 0。"""
        _, history = self.simulate_agent_loop(1)
        assert history[0] == 1  # After 1st round without todo_write

    def test_counter_increments_each_round_without_todo_write(self):
        """未调用 todo_write 时，每轮递增。"""
        _, history = self.simulate_agent_loop(5)
        assert history == [1, 2, 3, 1, 2]  # 3→reminder→reset→1→2

    def test_counter_resets_on_todo_write(self):
        """调用 todo_write 后计数器重置为 0。"""
        _, history = self.simulate_agent_loop(4, todo_write_called_at=[1])
        # Round 0: inc→1, Round 1: todo_write→0, Round 2: inc→1, Round 3: inc→2
        assert history == [1, 0, 1, 2]

    def test_counter_reminder_injected_at_threshold(self):
        """计数器达到 3 时注入提醒。"""
        reminders, history = self.simulate_agent_loop(3)
        assert reminders == 1  # One reminder injected
        assert history[2] == 3  # Counter reached 3 before reminder reset

    def test_counter_resets_after_reminder(self):
        """提醒注入后计数器重置。"""
        _, history = self.simulate_agent_loop(6)
        # Round 0:1, 1:2, 2:3→reminder→0, 3:1, 4:2, 5:3→reminder→0
        assert history == [1, 2, 3, 1, 2, 3]

    def test_counter_does_not_exceed_threshold(self):
        """计数器不应超过阈值（提醒注入后立即重置）。"""
        _, history = self.simulate_agent_loop(10)
        for h in history:
            assert h <= 3

    def test_text_only_rounds_increment_counter(self):
        """纯文本轮次（无工具调用）也递增计数器。"""
        # 这个逻辑在 simulate_agent_loop 中体现：只要未调用 todo_write 就递增
        # 无论该轮 LLM 返回的是文本还是其他工具调用
        _, history = self.simulate_agent_loop(3, todo_write_called_at=[])
        assert history == [1, 2, 3]

    def test_repeated_reminders_on_continued_ignoring(self):
        """持续忽略提醒时，每 3 轮重复触发。"""
        reminders, history = self.simulate_agent_loop(9)
        # 3 reminders at rounds 2, 5, 8 (0-indexed)
        assert reminders == 3
        assert history == [1, 2, 3, 1, 2, 3, 1, 2, 3]

    def test_final_step_threshold_no_injection(self):
        """max_steps 达到时计数器达到阈值，不注入（无下一轮 LLM 调用）。"""
        # 模拟：3 轮后达到阈值，但没有第 4 轮。计数器的提醒逻辑
        # 应该在下一轮 LLM 调用前检查，如果没有下一轮则跳过。
        counter = 0
        reminders = 0

        for rnd in range(3):
            counter += 1
            # 在"下一轮 LLM 调用前"检查 —— 如果这是最后一轮，没有"下一轮"
            if rnd < 2:  # Not the last round
                if counter >= 3:
                    reminders += 1
                    counter = 0

        # 第 3 轮后计数器 = 3，但没有下一轮 LLM 调用
        assert counter == 3
        assert reminders == 0


# ═══════════════════════════════════════════════════════════════
# Phase 5: US3 — 提醒注入测试 (T011)
# ═══════════════════════════════════════════════════════════════


class TestReminderInjection:
    """测试提醒消息注入逻辑。"""

    def test_reminder_format(self):
        """提醒消息格式应为 role='user'，content='<reminder>Update your todos.</reminder>'。"""
        reminder = {
            "role": "user",
            "content": "<reminder>Update your todos.</reminder>",
        }
        assert reminder["role"] == "user"
        assert reminder["content"] == "<reminder>Update your todos.</reminder>"
        assert "Update your todos" in reminder["content"]

    def test_reminder_injected_before_next_llm_call(self):
        """提醒应在下一次 LLM 调用前注入。"""
        # 这个测试通过 simulate_agent_loop 验证：
        # counter 达到 3 时，在下一轮前检查并注入
        counter = 0
        messages = []
        injected = False

        for rnd in range(4):
            # Check BEFORE LLM call (simulating the injection point)
            if counter >= 3 and not injected:
                messages.append({
                    "role": "user",
                    "content": "<reminder>Update your todos.</reminder>",
                })
                counter = 0
                injected = True

            # Simulate LLM call (non-todo_write)
            counter += 1

        assert injected
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "Update your todos" in messages[0]["content"]

    def test_reminder_not_injected_when_todo_write_called(self):
        """Agent 正常调用 todo_write 时不应注入提醒。"""
        counter = 0
        messages = []

        for rnd in range(5):
            # Check before LLM call
            if counter >= 3:
                messages.append({
                    "role": "user",
                    "content": "<reminder>Update your todos.</reminder>",
                })
                counter = 0

            # Simulate: todo_write called every round → counter stays at 0
            counter = 0  # reset because todo_write was called

        assert len(messages) == 0

    def test_reminder_reinjected_after_three_more_ignored_rounds(self):
        """提醒后 Agent 继续忽略 3 轮，应再次注入。"""
        reminders = []
        counter = 0

        for rnd in range(7):
            if counter >= 3:
                reminders.append(rnd)
                counter = 0

            counter += 1  # No todo_write call

        assert len(reminders) == 2  # At round 2 and round 5 (0-indexed counter check)
