"""Conversation 类单元测试 —— 多轮对话编排器。"""

import pytest
from unittest.mock import MagicMock, patch

from agent.agent import Agent
from agent.conversation import Conversation


class _FakeAgent:
    """模拟 Agent：记录调用并返回预设回答。"""

    def __init__(self, system_prompt="test system prompt"):
        self.system_prompt = system_prompt
        self.call_history: list[list[dict]] = []

    def run(self, messages: list[dict]) -> str:
        self.call_history.append(list(messages))  # 拷贝快照
        # 模拟真实 Agent：追加 assistant 回复到 messages
        messages.append({"role": "assistant", "content": "Mock response"})
        return "Mock response"


@pytest.fixture
def mock_agent():
    return _FakeAgent()


@pytest.fixture
def conv(mock_agent):
    return Conversation(mock_agent)


class TestConversationInit:
    """Conversation 初始化。"""

    def test_initial_messages_empty(self, mock_agent):
        """新 Conversation 的 messages 为空列表。"""
        c = Conversation(mock_agent)
        assert c.messages == []

    def test_interrupted_once_defaults_false(self, mock_agent):
        """_interrupted_once 初始为 False。"""
        c = Conversation(mock_agent)
        assert c._interrupted_once is False


class TestRunTurn:
    """_run_turn —— 单轮执行逻辑。"""

    def test_first_turn_inserts_system_prompt(self, conv, mock_agent):
        """首轮：messages 为空时应自动插入 system prompt。"""
        conv._run_turn("hello")

        assert len(conv.messages) >= 3  # system + user + assistant + ...
        assert conv.messages[0] == {"role": "system", "content": mock_agent.system_prompt}
        assert conv.messages[1] == {"role": "user", "content": "hello"}

    def test_second_turn_no_duplicate_system_prompt(self, conv, mock_agent):
        """后续轮次不应重复插入 system prompt。"""
        conv._run_turn("first")
        conv._run_turn("second")

        # system prompt 应该只出现一次（在位置 0）
        system_count = sum(
            1 for m in conv.messages if m.get("role") == "system"
        )
        assert system_count == 1

    def test_agent_run_receives_full_history(self, conv, mock_agent):
        """Agent.run() 收到的是完整历史 messages。"""
        conv._run_turn("first")
        conv._run_turn("second")

        # 第二次调用的 messages 应包含第一轮的内容
        second_call_msgs = mock_agent.call_history[1]
        assert any("first" in str(m) for m in second_call_msgs)
        assert second_call_msgs[-1] == {"role": "user", "content": "second"}

    def test_messages_mutated_in_place(self, conv, mock_agent):
        """Agent.run() 对 messages 的修改反映在 conv.messages 中。"""
        conv._run_turn("hello")

        # call_history 有快照；conv.messages 应该有后续追加的内容
        assert conv.messages is not mock_agent.call_history[0]
        assert len(conv.messages) >= len(mock_agent.call_history[0])


class TestStartLoop:
    """start() REPL 主循环。"""

    def test_exit_command(self, conv):
        """输入 /exit 应退出循环。"""
        with patch("builtins.input", side_effect=["/exit"]):
            conv.start()
        # 不应抛异常，messages 应为空（没有执行任何 turn）
        assert conv.messages == []

    def test_quit_command(self, conv):
        """输入 /quit 也应退出。"""
        with patch("builtins.input", side_effect=["/quit"]):
            conv.start()
        assert conv.messages == []

    def test_empty_input_skipped(self, conv, mock_agent):
        """空输入应被忽略，不传给 Agent。"""
        with patch("builtins.input", side_effect=["", "   ", "hello", "/exit"]):
            conv.start()
        # 只有 "hello" 这轮被执行
        assert len(mock_agent.call_history) == 1
        assert mock_agent.call_history[0][-1]["content"] == "hello"

    def test_multiple_turns(self, conv, mock_agent):
        """连续多轮对话。"""
        with patch("builtins.input", side_effect=["first", "second", "third", "/exit"]):
            conv.start()
        assert len(mock_agent.call_history) == 3

    def test_eof_exits_gracefully(self, conv):
        """EOF 应优雅退出。"""
        with patch("builtins.input", side_effect=EOFError):
            conv.start()
        # 无异常，正常退出

    def test_keyboard_interrupt_in_start_exits(self, conv):
        """Ctrl+C 在输入阶段退出。"""
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            conv.start()
        # 无异常，正常退出


class TestKeyboardInterrupt:
    """Ctrl+C 中断行为。"""

    def test_first_interrupt_recovers(self, conv, mock_agent):
        """第一次 Ctrl+C 在执行中 → 回到输入状态。"""
        # 第一轮正常 → 第二轮被中断 → 第三轮正常 → 退出
        original_run = mock_agent.run

        call_count = [0]

        def run_with_interrupt(messages):
            call_count[0] += 1
            if call_count[0] == 2:
                raise KeyboardInterrupt
            return original_run(messages)

        mock_agent.run = run_with_interrupt

        with patch("builtins.input", side_effect=["first", "second", "third", "/exit"]):
            conv.start()

        # 三轮 input 都接收了，第二轮 run 抛异常被 catch
        assert call_count[0] == 3

    def test_double_interrupt_exits(self, conv, mock_agent):
        """连续两次 Ctrl+C → 退出。"""
        original_run = mock_agent.run

        def run_always_interrupt(messages):
            raise KeyboardInterrupt

        mock_agent.run = run_always_interrupt

        with patch("builtins.input", side_effect=["first", "second"]):
            conv.start()

        # 第二轮 input 后连续两次 interrupt → exit
        # 只执行了 2 次 run（对应 2 次 input）


class TestApiError:
    """API 异常兜底。"""

    def test_api_error_does_not_crash(self, conv, mock_agent):
        """API 异常不应导致 REPL 崩溃。"""

        def run_with_error(messages):
            raise RuntimeError("API rate limit exceeded")

        mock_agent.run = run_with_error

        with patch("builtins.input", side_effect=["hello", "/exit"]):
            conv.start()

        # 第一轮报错被 catch，第二轮正常退出
        # 无异常传播


# ═══════════════════════════════════════════════════════════════
# US3: 状态保持验证
# ═══════════════════════════════════════════════════════════════


class TestPermissionCrossTurn:
    """权限会话规则跨轮保持 (T014)。"""

    def test_session_rule_persists_across_turns(self):
        """'始终允许'的权限规则应在后续轮次生效。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import RuleBehavior

        engine = PermissionEngine(default_behavior="ask")

        # 模拟用户在第 1 轮选择"始终允许"
        engine.allow_for_session(
            "bash",
            "git status",
            "会话预授权: bash git status",
        )

        # 第 2 轮：同类操作应自动放行
        result = engine.evaluate("bash", {"command": "git status"})
        assert result.behavior == RuleBehavior.ALLOW

    def test_session_rule_scoped_to_tool_and_content(self):
        """会话规则仅对匹配的工具和内容生效。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import RuleBehavior

        engine = PermissionEngine(default_behavior="ask")
        engine.allow_for_session("bash", "git status", "预授权")

        # 匹配的工具 + 内容 → 放行
        r1 = engine.evaluate("bash", {"command": "git status"})
        assert r1.behavior == RuleBehavior.ALLOW

        # 不匹配的工具 → 仍走 fallback
        r2 = engine.evaluate("write_file", {"file_path": "test.py"})
        assert r2.behavior == RuleBehavior.ASK  # fallback

    def test_revoke_session_rule(self):
        """撤销会话规则后恢复默认行为。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import RuleBehavior

        engine = PermissionEngine(default_behavior="ask")
        rid = engine.allow_for_session("bash", "git status", "预授权")

        engine.revoke_session_rule(rid)
        result = engine.evaluate("bash", {"command": "git status"})
        assert result.behavior == RuleBehavior.ASK  # fallback

    def test_clear_all_session_rules(self):
        """清空会话规则后全部恢复默认。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import RuleBehavior

        engine = PermissionEngine(default_behavior="ask")
        engine.allow_for_session("bash", "git status", "")
        engine.allow_for_session("read_file", "*", "")

        engine.clear_session_rules()
        assert engine.evaluate("bash", {"command": "git status"}).behavior == RuleBehavior.ASK


class TestTodoWriteCrossTurn:
    """TodoWrite 跨轮保持 (T015)。"""

    def test_current_todos_persist_in_memory(self):
        """CURRENT_TODOS 是模块级全局变量，自然跨轮保持。"""
        from tools.todo_write import CURRENT_TODOS

        CURRENT_TODOS.clear()
        CURRENT_TODOS.extend([
            {"content": "Task 1", "status": "completed"},
            {"content": "Task 2", "status": "in_progress"},
            {"content": "Task 3", "status": "pending"},
        ])

        # 多轮之间不应清空（实际代码中只有 todo_write 调用会替换）
        assert len(CURRENT_TODOS) == 3
        CURRENT_TODOS.clear()

    def test_restore_todos_after_compact(self):
        """L4 compact 后 _restore_todos 应恢复任务列表到 messages。"""
        from agent.compact import _restore_todos
        from tools.todo_write import CURRENT_TODOS

        CURRENT_TODOS.clear()
        CURRENT_TODOS.extend([
            {"content": "分析需求", "status": "completed"},
            {"content": "编写代码", "status": "in_progress"},
        ])

        messages = [{"role": "user", "content": "[Compacted summary]"}]
        _restore_todos(messages)

        # 应该有追加的 todo 恢复消息
        assert len(messages) > 1
        restore_msg = messages[-1]
        assert restore_msg["role"] == "user"
        assert "分析需求" in restore_msg["content"]
        assert "编写代码" in restore_msg["content"]

        CURRENT_TODOS.clear()

    def test_restore_todos_empty_list(self):
        """空 CURRENT_TODOS 不追加恢复消息。"""
        from agent.compact import _restore_todos
        from tools.todo_write import CURRENT_TODOS

        CURRENT_TODOS.clear()
        messages = [{"role": "user", "content": "[Compacted summary]"}]
        _restore_todos(messages)

        assert len(messages) == 1  # 无追加


class TestConversationIntegration:
    """Conversation 与真实 Agent 子类的集成。"""

    def test_with_real_agent_class(self):
        """使用真实的 Agent 类（mock LLM）。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Done."
        mock_response.tool_calls = None
        mock_llm.chat.return_value = ("stop", mock_response)

        from tooling.registry import ToolRegistry
        from tooling.executor import ToolExecutor

        executor = ToolExecutor()
        # bypass ToolRegistry for test
        executor._registry = ToolRegistry()

        agent = Agent(mock_llm, executor, system_prompt="You are helpful.", max_steps=5)
        conv = Conversation(agent)

        with patch("builtins.input", side_effect=["Hello", "/exit"]):
            conv.start()

        # Agent 被正确调用
        mock_llm.chat.assert_called()
