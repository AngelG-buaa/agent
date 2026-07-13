"""Session 持久化端到端集成测试。"""

import os
import tempfile

import pytest

from agent.session_manager import SessionManager


@pytest.fixture
def sm():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SessionManager(sessions_dir=tmpdir)
        yield mgr
        mgr.close()


class TestEndToEndPersistence:
    """端到端：创建 → 写入 → 恢复 → 验证。"""

    def test_full_roundtrip(self, sm):
        """完整持久化往返：system → user → assistant → tool → 恢复。"""
        sid = sm.create_session()

        # 模拟一次完整的对话轮次
        sm.save_message(sid, {"role": "system", "content": "You are helpful."})
        sm.save_message(sid, {"role": "user", "content": "列出当前目录"})
        sm.save_message(sid, {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_001",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"ls"}'},
            }],
        })
        sm.save_message(sid, {
            "role": "tool",
            "tool_call_id": "call_001",
            "content": "file1.py\nfile2.py",
        })
        sm.save_message(sid, {
            "role": "assistant",
            "content": "当前目录包含 file1.py 和 file2.py。",
        })

        # 恢复
        msgs = sm.load_messages(sid)
        assert len(msgs) == 5
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["tool_calls"][0]["function"]["name"] == "bash"
        assert msgs[3]["role"] == "tool"
        assert msgs[3]["tool_name"] == "bash"  # tool_name 从 assistant 反查
        assert msgs[3]["tool_call_id"] == "call_001"
        assert msgs[4]["role"] == "assistant"
        assert msgs[4]["content"] == "当前目录包含 file1.py 和 file2.py。"

    def test_empty_session_cleanup(self, sm):
        """空对话退出后被清理。"""
        sid = sm.create_session()
        # 只有 system + user 消息就算有内容了 — 不算空
        # 空对话 = 无 user 消息
        assert os.path.exists(os.path.join(sm.sessions_dir, f"{sid}.db"))
        sm.cleanup_if_empty(sid)
        assert not os.path.exists(os.path.join(sm.sessions_dir, f"{sid}.db"))

    def test_non_empty_session_not_cleaned(self, sm):
        """有 user 消息的 session 不被清理。"""
        sid = sm.create_session()
        sm.save_message(sid, {"role": "user", "content": "hello"})
        db_path = os.path.join(sm.sessions_dir, f"{sid}.db")
        assert os.path.exists(db_path)
        sm.cleanup_if_empty(sid)
        assert os.path.exists(db_path)

    def test_title_auto_set(self, sm):
        """首条 user 消息自动设置标题。"""
        sid = sm.create_session()
        sm.save_message(sid, {"role": "system", "content": "You are helpful."})
        sm.save_message(sid, {"role": "user", "content": "帮我写一个 HTTP 服务器"})
        sessions = sm.list_sessions()
        assert sessions[0].title == "帮我写一个 HTTP 服务器"

    def test_compact_does_not_lose_messages(self, sm):
        """验证 compact 不干扰持久化：写入的是原始消息（这个测试验证写入=原始）。"""
        sid = sm.create_session()
        original = "这是一条很长的用户输入 " * 50
        sm.save_message(sid, {"role": "user", "content": original})
        msgs = sm.load_messages(sid)
        assert msgs[0]["content"] == original  # 完整保留，未被截断
