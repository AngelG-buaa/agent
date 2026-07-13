"""SessionManager 单元测试 —— 用 tempfile (非 :memory:) 验证文件级行为。

:memory: 模式不适用于 SessionManager（需要独立 .db 文件 + os.remove + glob）。
使用 tempfile.TemporaryDirectory 隔离每个 test。
"""

import os
import sqlite3
import tempfile

import pytest

from agent.session_manager import SessionManager, SessionSummary


@pytest.fixture
def sm():
    """创建临时 sessions 目录的 SessionManager 实例。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SessionManager(sessions_dir=tmpdir)
        yield mgr
        mgr.close()  # 关闭所有连接，避免 teardown PermissionError


class TestCreateSession:
    """T003-T006: session CRUD"""

    def test_create_returns_uuid(self, sm):
        session_id = sm.create_session()
        assert len(session_id) == 36  # UUID4
        assert os.path.exists(os.path.join(sm.sessions_dir, f"{session_id}.db"))

    def test_create_initializes_schema(self, sm):
        session_id = sm.create_session()
        db_path = os.path.join(sm.sessions_dir, f"{session_id}.db")
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = {r[0] for r in tables}
        assert "sessions" in table_names
        assert "messages" in table_names
        assert "permissions" in table_names
        assert "todos" in table_names

    def test_create_inserts_session_row_with_untitled(self, sm):
        session_id = sm.create_session()
        with sm.get_connection(session_id) as conn:
            row = conn.execute("SELECT title, message_count FROM sessions WHERE id=?", (session_id,)).fetchone()
        assert row[0] == "Untitled"
        assert row[1] == 0


class TestListSessions:
    def test_list_returns_empty_initially(self, sm):
        assert sm.list_sessions() == []

    def test_list_returns_all_sorted_by_mtime(self, sm):
        import time
        sid1 = sm.create_session()
        time.sleep(0.1)
        sid2 = sm.create_session()
        time.sleep(0.1)
        sid3 = sm.create_session()
        sessions = sm.list_sessions()
        assert len(sessions) == 3
        # 按 mtime 降序: sid3 first
        assert sessions[0].id == sid3
        assert sessions[2].id == sid1

    def test_list_skips_corrupted_file(self, sm):
        # 创建一个非 db 的垃圾文件
        bad_path = os.path.join(sm.sessions_dir, "bad.db")
        with open(bad_path, "w") as f:
            f.write("not a database")
        sm.create_session()
        sessions = sm.list_sessions()
        assert all(s.id != "bad" for s in sessions)


class TestDeleteSession:
    def test_delete_removes_file(self, sm):
        sid = sm.create_session()
        sm.delete_session(sid)
        assert not os.path.exists(os.path.join(sm.sessions_dir, f"{sid}.db"))

    def test_delete_closes_connection(self, sm):
        sid = sm.create_session()
        sm.delete_session(sid)
        assert sid not in sm._connections


class TestRenameSession:
    def test_rename_updates_title(self, sm):
        sid = sm.create_session()
        sm.rename_session(sid, "New Title")
        sessions = sm.list_sessions()
        assert sessions[0].title == "New Title"


class TestSaveMessage:
    def test_save_persists_all_fields(self, sm):
        sid = sm.create_session()
        user_msg = {"role": "user", "content": "Hello, world!"}
        sm.save_message(sid, user_msg)

        with sm.get_connection(sid) as conn:
            row = conn.execute(
                "SELECT role, content, seq FROM messages WHERE session_id=? ORDER BY seq", (sid,)
            ).fetchone()
        assert row[0] == "user"
        assert row[1] == "Hello, world!"
        assert row[2] == 0

    def test_auto_title_on_first_user_message(self, sm):
        sid = sm.create_session()
        sm.save_message(sid, {"role": "system", "content": "You are helpful."})
        sm.save_message(sid, {"role": "user", "content": "帮我写一个 Python 的 HTTP 服务器"})
        sessions = sm.list_sessions()
        assert sessions[0].title == "帮我写一个 Python 的 HTTP 服务器"

    def test_auto_title_truncates_at_50_chars(self, sm):
        sid = sm.create_session()
        long_msg = "这是一个非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的消息"
        sm.save_message(sid, {"role": "user", "content": long_msg})
        sessions = sm.list_sessions()
        assert len(sessions[0].title) <= 50

    def test_auto_title_not_overwritten_by_second_user_msg(self, sm):
        sid = sm.create_session()
        sm.save_message(sid, {"role": "user", "content": "First message"})
        sm.save_message(sid, {"role": "assistant", "content": "OK"})
        sm.save_message(sid, {"role": "user", "content": "Second message"})
        sessions = sm.list_sessions()
        assert sessions[0].title == "First message"

    def test_save_updates_message_count(self, sm):
        sid = sm.create_session()
        sm.save_message(sid, {"role": "user", "content": "a"})
        sm.save_message(sid, {"role": "assistant", "content": "b"})
        sessions = sm.list_sessions()
        assert sessions[0].message_count == 2

    def test_save_tool_call_message(self, sm):
        sid = sm.create_session()
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_001",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"ls"}'},
            }],
        }
        sm.save_message(sid, assistant_msg)

        tool_msg = {"role": "tool", "tool_call_id": "call_001", "content": "file1.py\nfile2.py"}
        sm.save_message(sid, tool_msg)

        msgs = sm.load_messages(sid)
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["tool_calls"][0]["function"]["name"] == "bash"
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["tool_call_id"] == "call_001"

    def test_save_handles_sdk_object(self, sm):
        """验证 save_message 兼容 OpenAI SDK ChatCompletionMessage 对象。"""
        class FakeToolCall:
            def __init__(self):
                self.id = "call_sdk"
                self.type = "function"
                self.function = type("fn", (), {"name": "read_file", "arguments": '{}'})()

        class FakeMsg:
            role = "assistant"
            content = "I'll read the file."
            tool_calls = [FakeToolCall()]

        sid = sm.create_session()
        sm.save_message(sid, FakeMsg())
        msgs = sm.load_messages(sid)
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["tool_calls"][0]["id"] == "call_sdk"


class TestLoadMessages:
    def test_load_restores_tool_name(self, sm):
        sid = sm.create_session()
        sm.save_message(sid, {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "bash", "arguments": '{}'},
            }],
        })
        sm.save_message(sid, {"role": "tool", "tool_call_id": "call_abc", "content": "result"})
        msgs = sm.load_messages(sid)
        assert msgs[1]["tool_name"] == "bash"

    def test_load_empty_session(self, sm):
        sid = sm.create_session()
        assert sm.load_messages(sid) == []

    def test_load_preserves_seq_order(self, sm):
        sid = sm.create_session()
        for i, role in enumerate(["system", "user", "assistant", "user"]):
            sm.save_message(sid, {"role": role, "content": str(i)})
        msgs = sm.load_messages(sid)
        assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
        assert [m["content"] for m in msgs] == ["0", "1", "2", "3"]


class TestPermissions:
    def test_save_and_load(self, sm):
        sid = sm.create_session()
        sm.save_permission(sid, "bash")
        sm.save_permission(sid, "read_file")
        assert sm.load_permissions(sid) == {"bash", "read_file"}

    def test_insert_ignore_duplicate(self, sm):
        sid = sm.create_session()
        sm.save_permission(sid, "bash")
        sm.save_permission(sid, "bash")  # 不抛异常
        assert sm.load_permissions(sid) == {"bash"}


class TestTodos:
    def test_save_and_load(self, sm):
        sid = sm.create_session()
        todos = [
            {"content": "Task A", "status": "completed", "activeForm": "Completing Task A"},
            {"content": "Task B", "status": "in_progress", "activeForm": "Working on Task B"},
        ]
        sm.save_todos(sid, todos)
        loaded = sm.load_todos(sid)
        assert len(loaded) == 2
        assert loaded[0]["content"] == "Task A"
        assert loaded[0]["status"] == "completed"

    def test_save_overwrites_previous(self, sm):
        sid = sm.create_session()
        sm.save_todos(sid, [{"content": "Old", "status": "pending", "activeForm": "Old"}])
        sm.save_todos(sid, [{"content": "New", "status": "completed", "activeForm": "New"}])
        loaded = sm.load_todos(sid)
        assert len(loaded) == 1
        assert loaded[0]["content"] == "New"


class TestCleanupIfEmpty:
    def test_cleans_empty_session(self, sm):
        sid = sm.create_session()
        sm.cleanup_if_empty(sid)
        # 检查数据库文件被删除且连接已关闭
        assert not os.path.exists(os.path.join(sm.sessions_dir, f"{sid}.db"))
        assert sid not in sm._connections

    def test_keeps_non_empty_session(self, sm):
        sid = sm.create_session()
        sm.save_message(sid, {"role": "user", "content": "hello"})
        sm.cleanup_if_empty(sid)
        assert os.path.exists(os.path.join(sm.sessions_dir, f"{sid}.db"))


class TestClose:
    def test_close_clears_all_connections(self, sm):
        sid1 = sm.create_session()
        sid2 = sm.create_session()
        # 触发连接创建
        sm.save_message(sid1, {"role": "user", "content": "a"})
        sm.save_message(sid2, {"role": "user", "content": "b"})
        assert len(sm._connections) == 2
        sm.close()
        assert len(sm._connections) == 0
