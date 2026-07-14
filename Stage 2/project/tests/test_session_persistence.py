"""Session 持久化集成测试 —— 保护架构不变量。

测试通过真实消息链:
    Conversation → SessionController → Agent → on_message → SessionManager
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from agent.agent import Agent
from agent.conversation import Conversation
from agent.session_controller import SessionController
from agent.session_manager import (
    SessionManager,
    SessionError,
)
from tools.todo_write import (
    CURRENT_TODOS,
    replace_todos,
    snapshot_todos,
    register_todo_hooks,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_mock_engine():
    """创建模拟 PermissionEngine。"""
    engine = MagicMock()
    engine.set_grant_listener = MagicMock()
    engine.replace_session_rules = MagicMock()
    return engine


def _make_mock_agent():
    """创建模拟 Agent —— 返回简短答案。"""
    agent = MagicMock(spec=Agent)
    agent.system_prompt = "You are helpful."
    return agent


SYSTEM_MSG = {"role": "system", "content": "You are helpful."}


def _make_controller(mgr, engine):
    """快捷创建 SessionController（不再需要外部 todo_handle）。"""
    return SessionController(mgr, engine, SYSTEM_MSG)


# ------------------------------------------------------------------
# T040: Controller invariant —— /resume 取消不改变 active
# ------------------------------------------------------------------


class TestControllerInvariants:
    """SessionController 不变性测试。"""

    def test_cancel_resume_preserves_active(self):
        """/resume 取消 → active 不变。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            engine = _make_mock_engine()
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)

            # 创建并对话
            active1 = ctrl.start_new()
            sid1 = active1.id
            mgr.append_message(sid1, {"role": "user", "content": "hello A"})

            # 创建另一个 session
            sid2 = mgr.create_session({"role": "system", "content": "sys B"})
            mgr.append_message(sid2, {"role": "user", "content": "hello B"})

            # 尝试 switch —— 这次应该成功
            ctrl.switch(sid2)

            # 切回 sid1
            ctrl.switch(sid1)

            # active 应正确
            assert ctrl.active is not None
            assert ctrl.active.id == sid1
            # A 的原始消息还在
            contents = [
                m["content"] for m in ctrl.active.messages if m["role"] == "user"
            ]
            assert "hello A" in contents

            ctrl.close()

    def test_load_failure_preserves_active(self):
        """resume 目标损坏 → active 不变。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            engine = _make_mock_engine()
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)

            active1 = ctrl.start_new()
            sid1 = active1.id
            mgr.append_message(sid1, {"role": "user", "content": "hello"})

            # 尝试 resume 一个不存在的 session
            try:
                ctrl.resume("00000000-0000-0000-0000-000000000000")
            except SessionError:
                pass

            # active 不变
            assert ctrl.active is not None
            assert ctrl.active.id == sid1

            ctrl.close()

    def test_delete_other_while_active_preserves_active(self):
        """删除非 active session → active 不变。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            engine = _make_mock_engine()
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)

            active1 = ctrl.start_new()
            sid1 = active1.id
            mgr.append_message(sid1, {"role": "user", "content": "hello A"})

            # 创建另一个
            sid2 = mgr.create_session({"role": "system", "content": "sys B"})
            mgr.append_message(sid2, {"role": "user", "content": "hello B"})

            # 删除非 active → 成功
            ctrl.delete(sid2)

            # active 不变
            assert ctrl.active.id == sid1

            # 不能删除 active
            with pytest.raises(SessionError):
                ctrl.delete(sid1)

            ctrl.close()

    def test_start_new_keeps_grant_persistence_active(self):
        """新建第二个 session 后，Controller 的 grant listener 仍然有效。"""
        from tooling.permission.engine import PermissionEngine, PermissionGrant
        from tooling.permission.policy import PermissionRule, RuleBehavior

        ask_rule = PermissionRule(
            tool_name="bash",
            rule_behavior=RuleBehavior.ASK,
            rule_content="echo *",
            message="确认 echo",
            condition=lambda tool, params: tool == "bash",
            rule_id="ask-echo",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(tmpdir)
            engine = PermissionEngine([ask_rule], default_behavior="deny")
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)

            ctrl.start_new()
            second = ctrl.start_new()
            engine.allow_for_session(engine.evaluate("bash", {"command": "echo ok"}))

            assert mgr.load_session(second.id).permissions == [
                PermissionGrant("bash", "echo *")
            ]
            ctrl.close()

    def test_close_releases_registered_resources(self):
        """正常关闭时注销 Controller 持有的 Hook 和 listener。"""
        from hooks import HOOKS

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(tmpdir)
            engine = _make_mock_engine()
            baseline = {name: len(callbacks) for name, callbacks in HOOKS.items()}
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)
            ctrl.start_new()

            ctrl.close()

            assert ctrl.active is None
            assert {name: len(callbacks) for name, callbacks in HOOKS.items()} == baseline
            engine.set_grant_listener.assert_called_with(None)

    def test_first_user_message_updates_active_title(self):
        """首条 user 消息提交后，运行时标题与数据库标题保持一致。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(tmpdir)
            ctrl = SessionController(mgr, _make_mock_engine(), SYSTEM_MSG)
            ctrl.start_new()

            ctrl.append_message({"role": "user", "content": "A real title"})

            assert ctrl.active.title == "A real title"
            assert mgr.load_session(ctrl.active.id).title == ctrl.active.title
            ctrl.close()

    def test_grant_persistence_error_is_not_swallowed(self):
        """grant 未写入数据库时，不允许 listener 假装成功。"""
        from tooling.permission.engine import PermissionGrant

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(tmpdir)
            ctrl = SessionController(mgr, _make_mock_engine(), SYSTEM_MSG)
            ctrl.start_new()

            with patch.object(
                mgr,
                "save_grant",
                side_effect=SessionError("missing"),
            ):
                with pytest.raises(SessionError, match="missing"):
                    ctrl._on_grant(PermissionGrant("bash", "echo *"))

            ctrl.close()

    def test_todo_error_result_is_not_persisted(self):
        """todo_write 返回 error 时不提交 Todo 快照。"""
        from hooks import trigger_hooks

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(tmpdir)
            ctrl = SessionController(mgr, _make_mock_engine(), SYSTEM_MSG)
            ctrl.start_new()

            with patch.object(mgr, "save_todos") as save_todos:
                trigger_hooks("PostToolUse", "todo_write", {}, {"error": "invalid"})
                save_todos.assert_not_called()

            ctrl.close()


# ------------------------------------------------------------------
# T047: 权限跨 session 隔离
# ------------------------------------------------------------------


class TestPermissionIsolation:
    """A session 的 grant 不在 B session 生效。"""

    def test_grants_replaced_on_switch(self):
        """切换 session 时权限被正确替换。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            engine = _make_mock_engine()
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)

            # Session A: 创建并保存 grant
            ctrl.start_new()
            sid_a = ctrl.active.id
            mgr.append_message(sid_a, {"role": "user", "content": "hello A"})
            from tooling.permission.engine import PermissionGrant
            grant_a = PermissionGrant(tool_name="bash", rule_content="rule A")
            mgr.save_grant(sid_a, grant_a)

            # Session B: 创建
            sid_b = mgr.create_session({"role": "system", "content": "sys B"})
            mgr.append_message(sid_b, {"role": "user", "content": "hello B"})

            # 切换到 B → 权限被 B 的 grants 替换
            ctrl.switch(sid_b)
            engine.replace_session_rules.assert_called()

            # 切回 A → 权限被 A 的 grants 替换
            ctrl.switch(sid_a)
            engine.replace_session_rules.assert_called()

            ctrl.close()

    def test_new_session_replaces_rules(self):
        """start_new 清空权限。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            engine = _make_mock_engine()
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)

            # 有 grant 的 session
            ctrl.start_new()
            sid_a = ctrl.active.id
            mgr.append_message(sid_a, {"role": "user", "content": "hi"})
            from tooling.permission.engine import PermissionGrant
            mgr.save_grant(sid_a, PermissionGrant("bash", "rule"))

            # 新建 → 权限清空
            engine.replace_session_rules.reset_mock()
            ctrl.start_new()

            # replace_session_rules([]) 被调用
            engine.replace_session_rules.assert_called_with([])

            ctrl.close()


# ------------------------------------------------------------------
# T048: SubAgent 消息隔离
# ------------------------------------------------------------------


class TestSubAgentIsolation:
    """SubAgent 消息不进入主 session。"""

    def test_subagent_messages_not_in_main_session(self):
        """主 session 的 append_message 只被主 Agent 回调触发。

        SubAgent 使用默认 messages.append，不经过主 session callback。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            engine = _make_mock_engine()
            ctrl = SessionController(mgr, engine, SYSTEM_MSG)

            ctrl.start_new()
            sid = ctrl.active.id
            mgr.append_message(sid, {"role": "user", "content": "hello"})

            # 模拟主 Agent 的一轮回话 —— 只写入主 messages
            mgr.append_message(sid, {
                "role": "assistant",
                "content": "I'll use a task tool",
                "tool_calls": [{
                    "id": "task_001",
                    "type": "function",
                    "function": {"name": "task", "arguments": '{"prompt":"sub task"}'},
                }],
            })
            # tool result (task 工具的结果)
            mgr.append_message(sid, {
                "role": "tool",
                "tool_call_id": "task_001",
                "content": "sub result",
            })
            mgr.append_message(sid, {
                "role": "assistant",
                "content": "Done.",
            })

            snap = mgr.load_session(sid)
            roles = [m["role"] for m in snap.messages]
            # system, user, assistant(tool_calls), tool, assistant
            assert roles == ["system", "user", "assistant", "tool", "assistant"]

            # SubAgent 的中间消息不存在
            contents = [m.get("content", "") for m in snap.messages]
            # SubAgent 自身的 system prompt 不应该出现
            assert not any("你的任务是完成子任务" in str(c) for c in contents)

            ctrl.close()


# ------------------------------------------------------------------
# Empty session cleanup
# ------------------------------------------------------------------


class TestEmptySessionCleanup:
    """空对话退出自动清理。"""

    def test_empty_session_removed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            sid = mgr.create_session({"role": "system", "content": "test"})
            db_path = os.path.join(mgr.sessions_dir, f"{sid}.db")
            assert os.path.exists(db_path)

            mgr.cleanup_if_empty(sid)
            assert not os.path.exists(db_path)

    def test_nonempty_session_kept(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            sid = mgr.create_session({"role": "system", "content": "test"})
            mgr.append_message(sid, {"role": "user", "content": "hello"})

            db_path = os.path.join(mgr.sessions_dir, f"{sid}.db")
            assert os.path.exists(db_path)

            mgr.cleanup_if_empty(sid)
            assert os.path.exists(db_path)

    def test_auto_title_from_first_user_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(sessions_dir=tmpdir)
            sid = mgr.create_session({"role": "system", "content": "test"})
            # 还没有 user 消息 → Untitled
            snap = mgr.load_session(sid)
            assert snap.title == "Untitled"

            # 首条 user 消息
            mgr.append_message(sid, {"role": "user", "content": "帮我写一个 HTTP 服务器"})
            snap = mgr.load_session(sid)
            assert snap.title == "帮我写一个 HTTP 服务器"


# ------------------------------------------------------------------
# T012-T014: 真实持久化链路集成测试 —— Agent.run → on_message → Repository
# ------------------------------------------------------------------


class _FakeResponse:
    """Mock LLM 返回的 fake 消息对象。__slots__ 确保 hasattr(tool_call_id) 返回 False。"""
    __slots__ = ("role", "content", "tool_calls")

    def __init__(self, content=None, tool_calls=None):
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls


class _FakeToolCall:
    """Mock LLM 返回的 fake tool_call 对象。"""
    __slots__ = ("id", "type", "function")

    def __init__(self, tc_id, name, arguments):
        self.id = tc_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeFunction:
    __slots__ = ("name", "arguments")

    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class TestRealPersistenceChain:
    """覆盖 Agent.run → on_message → Controller → Repository 完整链路。"""

    def test_full_roundtrip_via_agent_run(self):
        """一轮无工具调用：final assistant 持久化且无重复消息。"""
        import tempfile
        from agent.agent import Agent
        from agent.session_manager import SessionManager
        from agent.session_controller import SessionController
        from tooling.permission.engine import PermissionEngine
        from tooling.executor import ToolExecutor
        from tools.todo_write import register_todo_hooks

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = os.path.join(tmpdir, "sessions")
            mgr = SessionManager(sessions_dir=sessions_dir)
            engine = PermissionEngine(default_behavior="allow")
            executor = ToolExecutor(
                permission_engine=engine,
                approver=lambda n, p, r: {"decision": "allow"},
            )
            from tools import register_all
            register_all(executor, include_dangerous=False, workdir=tmpdir, llm=None)

            system_msg = {"role": "system", "content": "You are helpful."}
            ctrl = SessionController(mgr, engine, system_msg)
            ctrl.start_new()

            # Mock LLM：返回 final answer（无工具调用）
            mock_llm = MagicMock()
            mock_llm.chat.return_value = (
                "stop",
                _FakeResponse(content="Hello, I am Claude."),
            )

            agent = Agent(mock_llm, executor, system_prompt="You are helpful.", max_steps=5)

            # 注入 user 消息 + Agent.run
            user_msg = {"role": "user", "content": "hi"}
            ctrl.append_message(user_msg)

            answer = agent.run(
                ctrl.active.messages,
                on_message=lambda m: ctrl.append_message(m),
            )

            # 验证
            assert answer == "Hello, I am Claude."

            snap = mgr.load_session(ctrl.active.id)
            assert snap.message_count == 3  # system + user + assistant
            roles = [m["role"] for m in snap.messages]
            assert roles == ["system", "user", "assistant"]

            assert snap.messages[0]["content"] == "You are helpful."
            assert snap.messages[2]["content"] == "Hello, I am Claude."

            # 无重复消息
            assert len(ctrl.active.messages) == snap.message_count
            for i, (mem, db) in enumerate(zip(ctrl.active.messages, snap.messages)):
                assert mem == db, f"消息 {i}: 内存与 DB 不一致"

            # 字段契约（FR-020）
            for m in snap.messages:
                assert set(m.keys()).issubset(
                    {"role", "content", "tool_calls", "tool_call_id"}
                ), f"非预期字段: {m.keys()}"

            ctrl.close()

    def test_tool_roundtrip_via_agent_run(self):
        """工具调用轮次：assistant(tool_calls) → tool → final assistant 完整持久化。"""
        import tempfile
        import json
        from agent.agent import Agent
        from agent.session_manager import SessionManager
        from agent.session_controller import SessionController
        from tooling.permission.engine import PermissionEngine
        from tooling.executor import ToolExecutor
        from tools.todo_write import register_todo_hooks

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = os.path.join(tmpdir, "sessions")
            mgr = SessionManager(sessions_dir=sessions_dir)
            engine = PermissionEngine(default_behavior="allow")
            executor = ToolExecutor(
                permission_engine=engine,
                approver=lambda n, p, r: {"decision": "allow"},
            )
            from tools import register_all
            register_all(executor, include_dangerous=False, workdir=tmpdir, llm=None)

            system_msg = {"role": "system", "content": "You are helpful."}
            ctrl = SessionController(mgr, engine, system_msg)
            ctrl.start_new()

            # Mock LLM：第一次返回 tool_calls，第二次返回 final
            mock_llm = MagicMock()
            mock_llm.chat.side_effect = [
                (
                    "tool_calls",
                    _FakeResponse(
                        content=None,
                        tool_calls=[
                            _FakeToolCall("call_001", "bash",
                                          json.dumps({"command": "echo hello"})),
                        ],
                    ),
                ),
                (
                    "stop",
                    _FakeResponse(content="Command executed successfully."),
                ),
            ]

            agent = Agent(mock_llm, executor, system_prompt="You are helpful.", max_steps=5)

            user_msg = {"role": "user", "content": "run echo hello"}
            ctrl.append_message(user_msg)

            answer = agent.run(
                ctrl.active.messages,
                on_message=lambda m: ctrl.append_message(m),
            )

            snap = mgr.load_session(ctrl.active.id)
            roles = [m["role"] for m in snap.messages]
            assert roles == ["system", "user", "assistant", "tool", "assistant"], (
                f"实际角色序列: {roles}"
            )

            # tool 消息无 tool_name
            tool_msg_db = snap.messages[3]
            assert tool_msg_db["tool_call_id"] == "call_001"
            assert "tool_name" not in tool_msg_db

            # final assistant 已持久化
            assert snap.messages[4]["content"] == "Command executed successfully."

            # 无重复
            assert len(ctrl.active.messages) == snap.message_count

            ctrl.close()

    def test_subagent_messages_not_persisted(self):
        """SubAgent 中间消息不进入主 session DB。"""
        import tempfile
        from agent.agent import SubAgent
        from agent.session_manager import SessionManager
        from agent.session_controller import SessionController
        from tooling.permission.engine import PermissionEngine
        from tooling.executor import ToolExecutor

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = os.path.join(tmpdir, "sessions")
            mgr = SessionManager(sessions_dir=sessions_dir)
            engine = PermissionEngine(default_behavior="allow")
            executor = ToolExecutor(
                permission_engine=engine,
                approver=lambda n, p, r: {"decision": "allow"},
            )
            from tools import register_all
            register_all(executor, include_dangerous=False, workdir=tmpdir, llm=None)

            ctrl = SessionController(mgr, engine, SYSTEM_MSG)
            ctrl.start_new()
            ctrl.append_message({"role": "user", "content": "do complex task"})

            mock_llm = MagicMock()
            mock_llm.chat.return_value = ("stop", _FakeResponse(content="sub done"))
            sub = SubAgent(llm=mock_llm, executor=executor)
            sub_messages = [
                {"role": "system", "content": "你是子任务执行者"},
                {"role": "user", "content": "complete sub task"},
            ]
            sub.run(sub_messages)

            snap = mgr.load_session(ctrl.active.id)
            db_roles = [m["role"] for m in snap.messages]
            assert db_roles == ["system", "user"], (
                f"SubAgent 消息泄漏到主 DB: {db_roles}"
            )
            contents = [m.get("content", "") for m in snap.messages]
            assert not any("子任务执行者" in str(c) for c in contents)

            ctrl.close()
