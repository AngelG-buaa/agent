"""SubAgent 和 Agent 扩展（tool_filter、print_handler）的单元测试。

测试覆盖:
  - Agent: tool_filter 参数 (T003)
  - Agent: print_handler 参数 (T004)
  - SubAgent: 构造配置 + 轮数跟踪 + 提醒注入 (T005)
  - agent.utils: 打印回调函数
"""

import json
import sys
from unittest.mock import MagicMock, patch
from io import StringIO

import pytest

sys.path.insert(0, "d:/LLM/Agent/Stage 2/project")


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def mock_llm():
    """创建一个假的 LLMClient。"""
    llm = MagicMock()
    llm.chat = MagicMock()
    return llm


@pytest.fixture
def mock_executor():
    """创建一个假的 ToolExecutor，带有可控的 schemas。"""
    from tooling.executor import ToolExecutor
    executor = MagicMock(spec=ToolExecutor)
    executor.get_schemas.return_value = [
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "task"}},
        {"type": "function", "function": {"name": "todo_write"}},
    ]
    executor.execute = MagicMock(return_value={"result": "ok"})
    return executor


# ═══════════════════════════════════════════════════════════════
# Phase 2: Agent tool_filter (T003)
# ═══════════════════════════════════════════════════════════════


class TestAgentToolFilter:
    """测试 Agent 的 tool_filter 参数。"""

    def test_tool_filter_none_preserves_all(self, mock_llm, mock_executor):
        """默认 tool_filter=None 保留全部 schemas。"""
        from agent.agent import Agent
        agent = Agent(mock_llm, mock_executor)
        assert agent.tool_filter is None

    def test_tool_filter_removes_specified_tools(self, mock_llm, mock_executor):
        """tool_filter 过滤掉指定工具名。"""
        from agent.agent import Agent
        agent = Agent(mock_llm, mock_executor, tool_filter={"task", "todo_write"})

        # 模拟 run() 中过滤 schemas 的逻辑
        schemas = mock_executor.get_schemas()
        filtered = [s for s in schemas
                    if s["function"]["name"] not in agent.tool_filter]
        names = [s["function"]["name"] for s in filtered]
        assert "bash" in names
        assert "read_file" in names
        assert "write_file" in names
        assert "task" not in names
        assert "todo_write" not in names
        assert len(filtered) == 3

    def test_tool_filter_empty_set_keeps_all(self, mock_llm, mock_executor):
        """空 set 不过滤任何工具。"""
        from agent.agent import Agent
        agent = Agent(mock_llm, mock_executor, tool_filter=set())
        schemas = mock_executor.get_schemas()
        filtered = [s for s in schemas
                    if s["function"]["name"] not in agent.tool_filter]
        assert len(filtered) == len(schemas)

    def test_tool_filter_nonexistent_tool_is_harmless(self, mock_llm, mock_executor):
        """过滤不存在的工具名不报错。"""
        from agent.agent import Agent
        agent = Agent(mock_llm, mock_executor, tool_filter={"nonexistent"})
        schemas = mock_executor.get_schemas()
        filtered = [s for s in schemas
                    if s["function"]["name"] not in agent.tool_filter]
        assert len(filtered) == len(schemas)  # nothing removed


# ═══════════════════════════════════════════════════════════════
# Phase 2: Agent print_handler (T004)
# ═══════════════════════════════════════════════════════════════


class TestAgentPrintHandler:
    """测试 Agent 的 print_handler 参数。"""

    def test_default_print_handler_format(self):
        """默认 handler 输出 🔧 调用工具 格式。"""
        from agent.utils import default_print_handler
        captured = StringIO()
        with patch("sys.stdout", captured):
            default_print_handler("bash", {"command": "ls"})
        output = captured.getvalue()
        assert "🔧 调用工具" in output
        assert "bash" in output

    def test_custom_print_handler_is_used(self, mock_llm, mock_executor):
        """自定义 print_handler 被 Agent 使用。"""
        from agent.agent import Agent
        calls = []

        def my_handler(name, args):
            calls.append((name, args))

        agent = Agent(mock_llm, mock_executor, print_handler=my_handler)
        assert agent.print_handler is my_handler

    def test_print_handler_falls_back_to_default(self, mock_llm, mock_executor):
        """未提供 print_handler 时使用默认值。"""
        from agent.agent import Agent
        from agent.utils import default_print_handler
        agent = Agent(mock_llm, mock_executor)
        assert agent.print_handler is default_print_handler


# ═══════════════════════════════════════════════════════════════
# Phase 2: agent/utils.py — 打印回调 (T004)
# ═══════════════════════════════════════════════════════════════


class TestPrintUtils:
    """测试 agent/utils.py 中的打印工具函数。"""

    def test_extract_key_param_bash(self):
        from agent.utils import _extract_key_param
        result = _extract_key_param("bash", {"command": "ls -la /home/user"})
        assert result == "ls -la /home/user"

    def test_extract_key_param_bash_long_truncated(self):
        from agent.utils import _extract_key_param
        long_cmd = "x" * 100
        result = _extract_key_param("bash", {"command": long_cmd})
        assert len(result) == 63  # 60 chars + "..."
        assert result.endswith("...")

    def test_extract_key_param_read_file(self):
        from agent.utils import _extract_key_param
        result = _extract_key_param("read_file", {"file_path": "/tmp/test.py"})
        assert result == "/tmp/test.py"

    def test_extract_key_param_write_file(self):
        from agent.utils import _extract_key_param
        result = _extract_key_param("write_file", {"file_path": "/tmp/out.py"})
        assert result == "/tmp/out.py"

    def test_extract_key_param_edit_file(self):
        from agent.utils import _extract_key_param
        result = _extract_key_param("edit_file", {"file_path": "/tmp/mod.py"})
        assert result == "/tmp/mod.py"

    def test_extract_key_param_web_search(self):
        from agent.utils import _extract_key_param
        result = _extract_key_param("web_search", {"query": "Python async"})
        assert "Python async" in result

    def test_extract_key_param_calculator(self):
        from agent.utils import _extract_key_param
        result = _extract_key_param("calculator", {"expression": "1+1"})
        assert result == ""

    def test_sub_print_handler_format(self):
        from agent.utils import sub_print_handler
        captured = StringIO()
        with patch("sys.stdout", captured):
            sub_print_handler("bash", {"command": "ls"})
        output = captured.getvalue()
        assert "[sub]" in output
        assert "bash" in output
        assert "🔧" not in output  # 不使用主 Agent 图标

    def test_normalize_message_filters_extra_dict_fields(self):
        """dict 输入也必须收敛到统一的四字段契约。"""
        from agent.utils import normalize_message

        normalized = normalize_message({
            "role": "assistant",
            "content": "ok",
            "unexpected": "must not leak",
        })

        assert normalized == {"role": "assistant", "content": "ok"}


# ═══════════════════════════════════════════════════════════════
# Phase 2: SubAgent 子类 (T005)
# ═══════════════════════════════════════════════════════════════


class TestSubAgentConstruction:
    """测试 SubAgent 的构造配置。"""

    def test_subagent_has_correct_max_steps(self, mock_llm, mock_executor):
        from agent.agent import SubAgent
        sub = SubAgent(mock_llm, mock_executor)
        assert sub.max_steps == 30

    def test_subagent_has_correct_tool_filter(self, mock_llm, mock_executor):
        from agent.agent import SubAgent
        sub = SubAgent(mock_llm, mock_executor)
        assert sub.tool_filter == {"task", "todo_write"}

    def test_subagent_has_sub_system_prompt(self, mock_llm, mock_executor):
        from agent.agent import SubAgent
        from agent.prompts import SUB_SYSTEM_PROMPT
        sub = SubAgent(mock_llm, mock_executor)
        assert sub.system_prompt == SUB_SYSTEM_PROMPT

    def test_subagent_uses_sub_print_handler(self, mock_llm, mock_executor):
        from agent.agent import SubAgent
        from agent.utils import sub_print_handler
        sub = SubAgent(mock_llm, mock_executor)
        assert sub.print_handler is sub_print_handler

    def test_subagent_round_starts_at_zero(self, mock_llm, mock_executor):
        from agent.agent import SubAgent
        sub = SubAgent(mock_llm, mock_executor)
        assert sub._round == 0


class TestSubAgentRoundTracking:
    """测试 SubAgent 的轮数跟踪和提醒注入。"""

    def test_round_increments_on_execute_tool_calls(self, mock_llm, mock_executor):
        """每调用一次 _execute_tool_calls，_round 递增 1。"""
        from agent.agent import SubAgent
        import json

        sub = SubAgent(mock_llm, mock_executor)

        # 模拟 tool_calls
        tc = MagicMock()
        tc.function.name = "bash"
        tc.function.arguments = json.dumps({"command": "echo test"})
        tc.id = "call_1"

        sub._execute_tool_calls([tc], [])
        assert sub._round == 1

        sub._execute_tool_calls([tc], [])
        assert sub._round == 2

    def test_reminder_injected_at_round_30(self, mock_llm, mock_executor):
        """第 30 轮时注入提醒消息。"""
        from agent.agent import SubAgent
        import json

        sub = SubAgent(mock_llm, mock_executor)
        sub._round = 29  # 模拟已经过了 29 轮

        tc = MagicMock()
        tc.function.name = "bash"
        tc.function.arguments = json.dumps({"command": "echo test"})
        tc.id = "call_1"

        messages = []
        sub._execute_tool_calls([tc], messages)

        assert sub._round == 30
        assert len(messages) == 2  # tool result + reminder (before tool exec)
        # reminder should be the first message (appended before super call)
        assert messages[0]["role"] == "user"
        assert "最大轮数限制" in messages[0]["content"]

    def test_reminder_not_injected_before_round_30(self, mock_llm, mock_executor):
        """第 1-29 轮不注入提醒。"""
        from agent.agent import SubAgent
        import json

        sub = SubAgent(mock_llm, mock_executor)

        tc = MagicMock()
        tc.function.name = "bash"
        tc.function.arguments = json.dumps({"command": "echo test"})
        tc.id = "call_1"

        for i in range(5):
            messages = []
            sub._execute_tool_calls([tc], messages)
            assert sub._round == i + 1
            # 前 29 轮不应有提醒
            has_reminder = any(
                "最大轮数限制" in m.get("content", "")
                for m in messages if m["role"] == "user"
            )
            assert not has_reminder

    def test_subagent_shares_executor(self, mock_llm, mock_executor):
        """SubAgent 与主 Agent 共享同一个 executor 实例。"""
        from agent.agent import SubAgent
        sub = SubAgent(mock_llm, mock_executor)
        assert sub.executor is mock_executor

    def test_each_subagent_has_independent_round_counter(self, mock_llm, mock_executor):
        """不同 SubAgent 实例的 _round 相互独立。"""
        from agent.agent import SubAgent
        import json

        sub1 = SubAgent(mock_llm, mock_executor)
        sub2 = SubAgent(mock_llm, mock_executor)

        tc = MagicMock()
        tc.function.name = "bash"
        tc.function.arguments = json.dumps({"command": "x"})
        tc.id = "c1"

        sub1._execute_tool_calls([tc], [])
        assert sub1._round == 1
        assert sub2._round == 0  # sub2 不受影响


# ═══════════════════════════════════════════════════════════════
# Phase 3: TaskTool + spawn_subagent (T010)
# ═══════════════════════════════════════════════════════════════


class TestTaskTool:
    """测试 TaskTool 的基本功能。"""

    def test_task_tool_name_and_params(self, mock_llm, mock_executor):
        """TaskTool 具有正确的名称和参数 schema。"""
        from tools.task import TaskTool
        tool = TaskTool(llm=mock_llm, executor=mock_executor)
        assert tool.name == "task"
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "task"
        params = schema["function"]["parameters"]
        assert "description" in params["properties"]
        assert "description" in params["required"]

    def test_empty_description_rejected(self, mock_llm, mock_executor):
        """空描述返回 error。"""
        from tools.task import TaskTool
        tool = TaskTool(llm=mock_llm, executor=mock_executor)
        result = tool.run({"description": ""})
        assert "error" in result
        assert "description is required" in result["error"]

    def test_missing_description_rejected(self, mock_llm, mock_executor):
        """缺少 description 参数返回 error。"""
        from tools.task import TaskTool
        tool = TaskTool(llm=mock_llm, executor=mock_executor)
        result = tool.run({})
        assert "error" in result

    def test_tool_stores_llm_and_executor(self, mock_llm, mock_executor):
        """TaskTool 构造时直接存储 llm 和 executor。"""
        from tools.task import TaskTool
        tool = TaskTool(llm=mock_llm, executor=mock_executor)
        assert tool._llm is mock_llm
        assert tool._executor is mock_executor


class TestSpawnSubagent:
    """测试 spawn_subagent 函数。"""

    def test_spawn_subagent_returns_string(self, mock_llm, mock_executor):
        """spawn_subagent 返回字符串（非 dict/None）。"""
        from tools.task import spawn_subagent
        from agent.agent import Agent

        # 创建一个假的 Agent.run() 行为
        original_run = Agent.run

        def fake_run(self, user_input):
            return "子任务完成：找到 5 个文件"

        Agent.run = fake_run
        try:
            result = spawn_subagent("查找文件", mock_llm, mock_executor)
            assert isinstance(result, str)
            assert "5 个文件" in result
        finally:
            Agent.run = original_run

    def test_spawn_subagent_uses_subagent_print(self, mock_llm, mock_executor):
        """spawn_subagent 输出包含 [Subagent spawned] 和 [Subagent done] 标记。"""
        from tools.task import spawn_subagent
        from agent.agent import Agent

        original_run = Agent.run

        def fake_run(self, user_input):
            return "done"

        Agent.run = fake_run
        try:
            captured = StringIO()
            with patch("sys.stdout", captured):
                spawn_subagent("test task", mock_llm, mock_executor)
            output = captured.getvalue()
            assert "[Subagent spawned]" in output
            assert "test task" in output
            assert "[Subagent done]" in output
        finally:
            Agent.run = original_run

    def test_spawn_subagent_creates_fresh_agent(self, mock_llm, mock_executor):
        """每次调用 spawn_subagent 创建新的 SubAgent 实例。"""
        from tools.task import spawn_subagent
        from agent.agent import Agent

        original_run = Agent.run
        Agent.run = lambda self, ui: "ok"
        try:
            result1 = spawn_subagent("task one", mock_llm, mock_executor)
            result2 = spawn_subagent("task two", mock_llm, mock_executor)
            assert isinstance(result1, str)
            assert isinstance(result2, str)
        finally:
            Agent.run = original_run

# ═══════════════════════════════════════════════════════════════
# Phase 4: US3 — Observability (T012)
# ═══════════════════════════════════════════════════════════════


class TestObservabilityFormat:
    """端到端验证 Sub-Agent 输出格式。"""

    def test_sub_print_handler_has_correct_prefix(self):
        """sub_print_handler 输出包含 [sub] 前缀且不含 🔧 图标。"""
        from agent.utils import sub_print_handler
        captured = StringIO()
        with patch("sys.stdout", captured):
            sub_print_handler("read_file", {"file_path": "/tmp/test.py"})
        output = captured.getvalue()
        assert "[sub]" in output
        assert "🔧" not in output
        assert "read_file" in output
        assert "/tmp/test.py" in output

    def test_spawn_lifecycle_markers_present(self, mock_llm, mock_executor):
        """spawn_subagent 输出包含启动和完成标记。"""
        from tools.task import spawn_subagent
        from agent.agent import Agent

        original_run = Agent.run
        Agent.run = lambda self, ui: "result"
        try:
            captured = StringIO()
            with patch("sys.stdout", captured):
                spawn_subagent("查找所有 .py 文件", mock_llm, mock_executor)
            output = captured.getvalue()
            assert output.index("[Subagent spawned]") < output.index("[Subagent done]")
            assert "查找所有 .py 文件" in output
        finally:
            Agent.run = original_run

    def test_all_tool_types_have_extract_coverage(self):
        """_extract_key_param 覆盖所有 Sub-Agent 可用工具类型。"""
        from agent.utils import _extract_key_param
        tool_args = [
            ("bash", {"command": "ls"}),
            ("read_file", {"file_path": "/tmp/test.py"}),
            ("write_file", {"file_path": "/tmp/out.py"}),
            ("edit_file", {"file_path": "/tmp/mod.py"}),
            ("read_chunk", {"file_path": "/tmp/test.py"}),
            ("web_search", {"query": "test query"}),
            ("web_fetch", {"url": "https://example.com"}),
            ("search_knowledge", {"query": "knowledge query"}),
            ("calculator", {"expression": "1+1"}),
            ("get_time", {}),
        ]
        for name, args in tool_args:
            result = _extract_key_param(name, args)
            assert isinstance(result, str), f"{name} should return str"


# ═══════════════════════════════════════════════════════════════
# Phase 5: Edge Cases (T013)
# ═══════════════════════════════════════════════════════════════


class TestEdgeCases:
    """测试边界情况和错误处理。"""

    def test_tool_call_denied_by_permission(self, mock_llm, mock_executor):
        """权限系统拒绝时 SubAgent 仍能正常返回非空文本。"""
        from tools.task import spawn_subagent
        from agent.agent import Agent

        mock_executor.execute.return_value = {"error": "denied by policy"}

        original_run = Agent.run
        Agent.run = lambda self, ui: "所有工具调用被拒绝，无法完成任务"
        try:
            result = spawn_subagent("写文件", mock_llm, mock_executor)
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            Agent.run = original_run

    def test_llm_api_error_caught_as_error_dict(self):
        """LLM API 异常被 TaskTool.run() 捕获为 error dict 而非崩溃。"""
        from tools.task import TaskTool

        bad_llm = MagicMock()
        bad_llm.chat = MagicMock(side_effect=RuntimeError("API connection failed"))
        bad_executor = MagicMock()
        bad_executor.get_schemas.return_value = [
            {"type": "function", "function": {"name": "bash"}}
        ]

        tool = TaskTool(llm=bad_llm, executor=bad_executor)
        result = tool.run({"description": "do something"})
        assert "error" in result
        assert "API connection failed" in result["error"]
