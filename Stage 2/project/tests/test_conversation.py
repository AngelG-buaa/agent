"""Conversation 类单元测试 —— 多轮对话编排器。"""

import pytest
import tempfile
from unittest.mock import MagicMock, patch

from agent.agent import Agent
from agent.conversation import Conversation


class _FakeAgent:
    """模拟 Agent：记录调用并返回预设回答。"""

    def __init__(self, system_prompt="test system prompt"):
        self.system_prompt = system_prompt
        self.call_history: list[list[dict]] = []

    def run(self, messages: list[dict], on_message=None) -> str:
        self.call_history.append(list(messages))  # 拷贝快照
        # 模拟真实 Agent：使用 on_message sink 追加 assistant 回复
        msg = {"role": "assistant", "content": "Mock response"}
        if on_message is not None:
            on_message(msg)
        else:
            messages.append(msg)
        return "Mock response"


@pytest.fixture
def mock_agent():
    return _FakeAgent()


@pytest.fixture
def conv(mock_agent, tmp_path):
    conv = _make_conversation(mock_agent, tmp_path)
    conv._controller.start_new()
    return conv


def _make_conversation(agent, sessions_dir):
    from agent.session_manager import SessionManager
    from tooling.permission.engine import PermissionEngine

    return Conversation(
        agent,
        session_manager=SessionManager(str(sessions_dir)),
        permission_engine=PermissionEngine(default_behavior="allow"),
        system_message={"role": "system", "content": agent.system_prompt},
    )


class TestConversationInit:
    """Conversation 初始化。"""

    def test_conversation_does_not_own_messages(self, mock_agent, tmp_path):
        """Conversation 不持有会话消息。"""
        c = _make_conversation(mock_agent, tmp_path)
        assert not hasattr(c, "messages")

    def test_interrupted_once_defaults_false(self, mock_agent, tmp_path):
        """_interrupted_once 初始为 False。"""
        c = _make_conversation(mock_agent, tmp_path)
        assert c._interrupted_once is False


class TestRunTurn:
    """_run_turn —— 单轮执行逻辑。"""

    def test_first_turn_inserts_system_prompt(self, conv, mock_agent):
        """新会话以 system prompt 开始，并记录首轮消息。"""
        conv._run_turn("hello")

        messages = conv._controller.active.messages
        assert len(messages) >= 3  # system + user + assistant + ...
        assert messages[0] == {"role": "system", "content": mock_agent.system_prompt}
        assert messages[1] == {"role": "user", "content": "hello"}

    def test_second_turn_no_duplicate_system_prompt(self, conv, mock_agent):
        """后续轮次不应重复插入 system prompt。"""
        conv._run_turn("first")
        conv._run_turn("second")

        # system prompt 应该只出现一次（在位置 0）
        system_count = sum(
            1 for m in conv._controller.active.messages if m.get("role") == "system"
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

    def test_agent_uses_active_session_messages(self, conv, mock_agent):
        """Agent 使用 ActiveSession 持有的 working context。"""
        conv._run_turn("hello")

        messages = conv._controller.active.messages
        assert messages is not mock_agent.call_history[0]
        assert messages[-1] == {"role": "assistant", "content": "Mock response"}


class TestStartLoop:
    """start() REPL 主循环。"""

    def test_exit_command(self, conv):
        """输入 /exit 应退出循环。"""
        with patch("builtins.input", side_effect=["/exit"]):
            conv.start()

    def test_start_accepts_resume_keyword(self, conv):
        """统一启动入口接受 resume=False。"""
        with patch("builtins.input", side_effect=["/exit"]):
            conv.start(resume=False)

    def test_quit_command(self, conv):
        """输入 /quit 也应退出。"""
        with patch("builtins.input", side_effect=["/quit"]):
            conv.start()

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

        def run_with_interrupt(messages, on_message=None):
            call_count[0] += 1
            if call_count[0] == 2:
                raise KeyboardInterrupt
            return original_run(messages, on_message=on_message)

        mock_agent.run = run_with_interrupt

        with patch("builtins.input", side_effect=["first", "second", "third", "/exit"]):
            conv.start()

        # 三轮 input 都接收了，第二轮 run 抛异常被 catch
        assert call_count[0] == 3

    def test_double_interrupt_exits(self, conv, mock_agent):
        """连续两次 Ctrl+C → 退出。"""
        original_run = mock_agent.run

        def run_always_interrupt(messages, on_message=None):
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


class TestSessionMenu:
    """启动菜单和 REPL 菜单共享的异常边界。"""

    def test_invalid_grant_does_not_replace_active_session(self, mock_agent):
        """恢复到含过期 grant 的 session 时留在当前 session。"""
        from agent.session_manager import SessionManager
        from tooling.permission.engine import PermissionEngine, PermissionGrant
        from tooling.permission.exceptions import InvalidPermissionGrant

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SessionManager(tmpdir)
            engine = PermissionEngine(default_behavior="allow")
            conv = Conversation(
                mock_agent,
                session_manager=manager,
                permission_engine=engine,
                system_message={"role": "system", "content": "system"},
            )
            current_id = conv._controller.start_new().id
            target_id = manager.create_session({"role": "system", "content": "system"})
            manager.save_grant(target_id, PermissionGrant("bash", "stale-rule"))

            with (
                patch("agent.ui.select_session", return_value=target_id),
                patch("agent.ui.show_actions_menu", return_value="resume"),
            ):
                with pytest.raises(InvalidPermissionGrant):
                    conv._session_menu(startup=False)

            assert conv._controller.active.id == current_id
            conv._controller.close()

    def test_rename_action_has_distinct_shortcut(self):
        """Rename 使用 n，不能与 Resume 的 r 冲突。"""
        from agent import ui

        with patch("builtins.input", return_value="n"):
            assert ui.show_actions_menu() == "rename"


# ═══════════════════════════════════════════════════════════════
# US3: 状态保持验证
# ═══════════════════════════════════════════════════════════════


class TestPermissionGrantFlow:
    """验证 grant 创建和替换的端到端流程（新 API）。"""

    def test_grant_created_via_allow_for_session(self):
        """通过 allow_for_session 创建 grant → evaluate 返回 ALLOW。"""
        from tooling.permission.engine import PermissionEngine, EvalResult
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="git status", message="确认 git 操作",
            condition=lambda t, p: "git status" in p.get("command", ""),
            rule_id="policy-ask-git",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        # 模拟用户选择 "session"
        eval_result = engine.evaluate("bash", {"command": "git status"})
        assert eval_result.behavior == RuleBehavior.ASK
        engine.allow_for_session(eval_result)

        # 后续同操作自动放行
        r2 = engine.evaluate("bash", {"command": "git status"})
        assert r2.behavior == RuleBehavior.ALLOW

    def test_grant_scoped_to_tool_and_content(self):
        """grant 仅对匹配的工具和内容生效。"""
        from tooling.permission.engine import PermissionEngine
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="git status", message="确认 git 操作",
            condition=lambda t, p: "git status" in p.get("command", ""),
            rule_id="policy-ask-git",
        )
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        eval_result = engine.evaluate("bash", {"command": "git status"})
        engine.allow_for_session(eval_result)

        # 匹配 → 放行
        r1 = engine.evaluate("bash", {"command": "git status"})
        assert r1.behavior == RuleBehavior.ALLOW

        # 不同工具 → fallback
        r2 = engine.evaluate("write_file", {"file_path": "test.py"})
        assert r2.behavior == RuleBehavior.DENY

        # 同工具不同命令 → fallback
        r3 = engine.evaluate("bash", {"command": "rm file.txt"})
        assert r3.behavior == RuleBehavior.DENY


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

    def test_with_real_agent_class(self, tmp_path):
        """使用真实的 Agent 类（mock LLM）。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Done."
        mock_response.tool_calls = None
        mock_llm.chat.return_value = ("stop", mock_response)

        from tooling.registry import ToolRegistry
        from tooling.executor import ToolExecutor
        from tooling.permission import PermissionEngine

        engine = PermissionEngine(default_behavior="allow")
        executor = ToolExecutor(permission_engine=engine, approver=lambda n,p,r: {"decision": "allow"})
        # bypass ToolRegistry for test
        executor._registry = ToolRegistry()

        agent = Agent(mock_llm, executor, system_prompt="You are helpful.", max_steps=5)
        conv = _make_conversation(agent, tmp_path)

        with patch("builtins.input", side_effect=["Hello", "/exit"]):
            conv.start()

        # Agent 被正确调用
        mock_llm.chat.assert_called()
