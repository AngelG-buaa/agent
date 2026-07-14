"""SessionManager Repository 核心测试 —— 三个案例保护架构不变量。"""

import os
import contextlib
import sqlite3
import tempfile
import uuid
from unittest.mock import patch

import pytest

from agent.session_manager import (
    SessionManager,
    SessionCorrupted,
    SessionError,
)


@pytest.fixture
def mgr():
    """每个测试独享 tmp sessions_dir。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        m = SessionManager(sessions_dir=tmpdir)
        yield m


# ------------------------------------------------------------------
# T021: 核心往返 —— create + append + load, 角色顺序
# ------------------------------------------------------------------


class TestRepositoryRoundtrip:
    """create → append → load 往返，验证角色顺序。"""

    def test_create_append_load_role_order(self, mgr):
        """system → user → assistant 顺序完整保留。"""
        sid = mgr.create_session({"role": "system", "content": "You are helpful."})

        mgr.append_message(sid, {"role": "user", "content": "hello"})
        mgr.append_message(sid, {"role": "assistant", "content": "Hi there!"})

        snap = mgr.load_session(sid)
        assert snap.message_count == 3
        assert snap.title != "Untitled"

        roles = [m["role"] for m in snap.messages]
        assert roles == ["system", "user", "assistant"]
        assert snap.messages[1]["content"] == "hello"

    def test_tool_roundtrip(self, mgr):
        """工具调用轮: assistant(tool_calls) → tool → assistant(final)。"""
        sid = mgr.create_session({"role": "system", "content": "You are helpful."})

        mgr.append_message(sid, {"role": "user", "content": "ls"})
        mgr.append_message(sid, {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_001",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"ls"}'},
            }],
        })
        mgr.append_message(sid, {
            "role": "tool",
            "tool_call_id": "call_001",
            "content": "file1.py",
        })
        mgr.append_message(sid, {
            "role": "assistant",
            "content": "当前目录: file1.py",
        })

        snap = mgr.load_session(sid)
        roles = [m["role"] for m in snap.messages]
        assert roles == ["system", "user", "assistant", "tool", "assistant"]

        # tool 消息无 tool_name 字段
        tool_msg = snap.messages[3]
        assert tool_msg["tool_call_id"] == "call_001"
        assert "tool_name" not in tool_msg

        # 恢复消息只含四个字段
        for m in snap.messages:
            assert set(m.keys()).issubset(
                {"role", "content", "tool_calls", "tool_call_id"}
            )


# ------------------------------------------------------------------
# T022: PermissionGrant + 空 Todo 往返
# ------------------------------------------------------------------


class TestGrantAndTodoRoundtrip:
    """验证 grant 和 Todo 的正确往返。"""

    def test_grant_roundtrip(self, mgr):
        """save_grant → load_session → grant 恢复一致。"""
        from tooling.permission.engine import PermissionGrant

        sid = mgr.create_session({"role": "system", "content": "test"})
        mgr.append_message(sid, {"role": "user", "content": "hi"})

        grant = PermissionGrant(tool_name="bash", rule_content="执行系统命令")
        mgr.save_grant(sid, grant)

        snap = mgr.load_session(sid)
        assert len(snap.permissions) == 1
        assert snap.permissions[0].tool_name == "bash"
        assert snap.permissions[0].rule_content == "执行系统命令"

    def test_empty_todo_roundtrip(self, mgr):
        """空 Todo 列表保存后恢复为 []。"""
        sid = mgr.create_session({"role": "system", "content": "test"})
        mgr.append_message(sid, {"role": "user", "content": "hi"})

        mgr.save_todos(sid, [])
        snap = mgr.load_session(sid)
        assert snap.todos == []

    def test_nonempty_todo_roundtrip(self, mgr):
        """非空 Todo 保存后恢复，含 position 顺序。"""
        sid = mgr.create_session({"role": "system", "content": "test"})
        mgr.append_message(sid, {"role": "user", "content": "hi"})

        todos = [
            {"content": "任务A", "status": "completed", "active_form": "做A", "position": 0},
            {"content": "任务B", "status": "in_progress", "active_form": "做B", "position": 1},
            {"content": "任务C", "status": "pending", "active_form": "做C", "position": 2},
        ]
        mgr.save_todos(sid, todos)

        snap = mgr.load_session(sid)
        assert snap.todos == todos


# ------------------------------------------------------------------
# T023: 损坏数据库被跳过且不残留文件锁
# ------------------------------------------------------------------


class TestCorruptedFileHandling:
    """损坏数据库跳过 + Windows 无连接泄漏。"""

    def test_corrupted_file_skipped_and_deletable(self, mgr):
        """创建纯文本 .db → list_sessions 跳过 → 文件可删除。"""
        # 创建合法 session
        sid = mgr.create_session({"role": "system", "content": "valid"})
        mgr.append_message(sid, {"role": "user", "content": "hi"})

        # 创建纯文本伪装 .db
        bad_id = str(uuid.uuid4())
        bad_path = os.path.join(mgr.sessions_dir, f"{bad_id}.db")
        with open(bad_path, "w") as f:
            f.write("this is not a database")

        try:
            sessions = mgr.list_sessions()
            assert len(sessions) == 1
            assert sessions[0].id == sid
            ids = [s.id for s in sessions]
            assert bad_id not in ids
        finally:
            if os.path.exists(bad_path):
                os.remove(bad_path)

    def test_corrupted_json_is_reported_as_session_corrupted(self, mgr):
        """消息 JSON 损坏时只暴露 Repository 领域异常。"""
        sid = mgr.create_session({"role": "system", "content": "valid"})
        mgr.append_message(sid, {
            "role": "assistant",
            "content": None,
            "tool_calls": [],
        })
        db_path = os.path.join(mgr.sessions_dir, f"{sid}.db")
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE messages SET tool_calls = 'not-json' WHERE role = 'assistant'"
                )

        with pytest.raises(SessionCorrupted, match="消息 JSON"):
            mgr.load_session(sid)

    def test_cleanup_query_error_never_deletes_database(self, mgr):
        """无法确认 session 为空时必须保留文件并报告错误。"""
        sid = str(uuid.uuid4())
        db_path = os.path.join(mgr.sessions_dir, f"{sid}.db")
        with open(db_path, "w", encoding="utf-8") as file:
            file.write("not a sqlite database")

        with pytest.raises(SessionError, match="清理空 session"):
            mgr.cleanup_if_empty(sid)
        assert os.path.exists(db_path)

    def test_mismatched_database_identity_is_corrupted(self, mgr):
        """文件名 UUID 与 sessions.id 不一致时拒绝构造混合快照。"""
        sid = mgr.create_session({"role": "system", "content": "valid"})
        db_path = os.path.join(mgr.sessions_dir, f"{sid}.db")
        other_id = str(uuid.uuid4())
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            with conn:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute("UPDATE sessions SET id = ?", (other_id,))

        with pytest.raises(SessionCorrupted, match="身份不一致"):
            mgr.load_session(sid)


class TestRepositoryTransactions:
    """Repository 的事务与 metadata 不变量。"""

    def test_load_session_explicitly_begins_read_transaction(self, mgr):
        """完整快照读取必须显式开启事务，不能依赖 SELECT 隐式行为。"""
        sid = mgr.create_session({"role": "system", "content": "valid"})
        statements: list[str] = []
        real_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch("agent.session_manager.sqlite3.connect", side_effect=traced_connect):
            mgr.load_session(sid)

        assert any(statement.strip().upper() == "BEGIN" for statement in statements)

    def test_whitespace_user_message_keeps_untitled_fallback(self, mgr):
        """纯空白首条 user 消息不能成为不可见标题。"""
        sid = mgr.create_session({"role": "system", "content": "valid"})
        mgr.append_message(sid, {"role": "user", "content": "   "})
        assert mgr.load_session(sid).title == "Untitled"
