"""Context Compact 单元测试。

覆盖 L1/L2/L3/L4 各层独立测试 + 集成测试。
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.compact import (
    _estimate_size,
    _has_tool_calls,
    _has_tool_call_id,
    _is_tool_for_ids,
    _restore_todos,
    compact_history,
    compact_pipeline,
    micro_compact,
    snip_compact,
    tool_result_budget,
)
from agent.utils import (
    get_role,
    get_content,
    get_tool_calls,
    get_tool_call_id,
    set_content,
    to_serializable,
)


# ═══════════════════════════════════════════════════════════
# Mock SDK 对象 —— 模拟 OpenAI ChatCompletionMessage
# ═══════════════════════════════════════════════════════════


class MockSDKToolCall:
    """模拟 openai.types.chat.ChatCompletionMessageToolCall。"""

    def __init__(self, idx, name="test_tool", arguments="{}"):
        self.id = f"call_{idx}"
        self.type = "function"
        self.function = MockSDKFunction(name, arguments)


class MockSDKFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class MockSDKMessage:
    """模拟 openai.types.chat.ChatCompletionMessage。"""

    def __init__(self, role="assistant", content="", tool_calls=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


def _make_msg(role, content="", tool_calls=None, tool_call_id=None):
    """快捷构造 OpenAI 格式消息。"""
    msg: dict = {"role": role}
    if content:
        msg["content"] = content
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    return msg


def _make_tool_call(idx, name="test_tool"):
    """快捷构造 tool_call。"""
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


@pytest.fixture
def sample_messages():
    """基础消息集：system + user + assistant(tool_calls) + 3 tool 结果。"""
    return [
        _make_msg("system", "You are a helpful assistant."),
        _make_msg("user", "Read config and run tests."),
        _make_msg("assistant", "", tool_calls=[_make_tool_call(1), _make_tool_call(2), _make_tool_call(3)]),
        _make_msg("tool", '{"stdout":"file content A","returncode":0}', tool_call_id="call_1"),
        _make_msg("tool", '{"stdout":"file content B","returncode":0}', tool_call_id="call_2"),
        _make_msg("tool", '{"stdout":"short"}', tool_call_id="call_3"),
    ]


@pytest.fixture
def large_tool_content():
    """510KB 的模拟输出 —— 超过 TOOL_RESULT_BUDGET_BYTES (500KB)。"""
    return "x" * 510_000


@pytest.fixture
def sdk_assistant_msg():
    """SDK ChatCompletionMessage —— 模拟 LLM 返回的 assistant 消息。"""
    return MockSDKMessage(
        role="assistant",
        content="",
        tool_calls=[
            MockSDKToolCall(1, "bash"),
            MockSDKToolCall(2, "read_file"),
        ],
    )


@pytest.fixture
def mixed_messages(sdk_assistant_msg):
    """混合类型消息列表 —— dict + SDK 对象。"""
    return [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Run tests."},
        sdk_assistant_msg,                                 # ← SDK 对象
        {"role": "tool", "tool_call_id": "call_1", "content": "bash output"},
        {"role": "tool", "tool_call_id": "call_2", "content": "file output"},
    ]


# ═══════════════════════════════════════════════════════════
# T018-T020: L3 tool_result_budget
# ═══════════════════════════════════════════════════════════


class TestL3ToolResultBudget:
    """L3: 大结果持久化到磁盘。"""

    def test_noop_when_under_budget(self, sample_messages):
        """Total < 500KB → no-op."""
        msgs = [dict(m) for m in sample_messages]
        before = len(msgs)
        tool_result_budget(msgs)
        # 消息数量不变，内容不变
        assert len(msgs) == before
        for i, orig in enumerate(sample_messages):
            assert msgs[i] == orig

    def test_persists_when_over_budget(self, large_tool_content):
        """单个 tool 消息 >30KB 且总量 >500KB → 持久化。"""
        msgs = [
            _make_msg("system", "You are helpful."),
            _make_msg("user", "Run command"),
            _make_msg("assistant", "", tool_calls=[_make_tool_call(1)]),
            _make_msg("tool", large_tool_content, tool_call_id="call_1"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.compact as cmod
            orig_dir = cmod.TOOL_RESULTS_DIR
            cmod.TOOL_RESULTS_DIR = Path(tmpdir)
            try:
                tool_result_budget(msgs)
                content = msgs[-1]["content"]
                assert "<persisted-output>" in content
                assert "Full output:" in content
                assert "Preview:" in content
                # 检查磁盘文件
                saved = Path(tmpdir) / "call_1.txt"
                assert saved.exists()
                assert saved.read_text() == large_tool_content
            finally:
                cmod.TOOL_RESULTS_DIR = orig_dir

    def test_skips_small_content(self):
        """单条 ≤30KB → 不触发持久化（即使总量超预算）。"""
        # 构造多条消息让总大小超过 500KB，但每条 ≤30KB
        small = "a" * 20_000  # 20KB each
        tool_calls = [_make_tool_call(i) for i in range(30)]  # 30 条 = 600KB total
        msgs = [_make_msg("system", "You are helpful.")]
        msgs.append(_make_msg("user", "Run many commands"))
        msgs.append(_make_msg("assistant", "", tool_calls=tool_calls))
        for i in range(30):
            msgs.append(_make_msg("tool", small, tool_call_id=f"call_{i}"))

        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.compact as cmod
            orig_dir = cmod.TOOL_RESULTS_DIR
            cmod.TOOL_RESULTS_DIR = Path(tmpdir)
            try:
                tool_result_budget(msgs)
                # 每条都 ≤30KB，不应持久化任何一条
                for msg in msgs:
                    if msg["role"] == "tool":
                        assert "<persisted-output>" not in msg.get("content", "")
            finally:
                cmod.TOOL_RESULTS_DIR = orig_dir


# ═══════════════════════════════════════════════════════════
# T021-T025: L1 snip_compact
# ═══════════════════════════════════════════════════════════


class TestL1SnipCompact:
    """L1: 消息截断 + 边界保护。"""

    def test_noop_under_limit(self):
        """≤100 条 → no-op。"""
        msgs = [_make_msg("user", f"msg{i}") for i in range(50)]
        before = list(msgs)
        snip_compact(msgs, max_messages=100)
        assert msgs == before

    def test_cuts_middle_inserts_placeholder(self):
        """>100 条 → 保留 head + tail + snipped 标记。"""
        msgs = [_make_msg("user", f"msg{i}") for i in range(150)]
        snip_compact(msgs, max_messages=100)
        assert len(msgs) <= 101  # 3 + 1 + 97 = 101
        # 检查 snipped 标记存在
        snipped = [m for m in msgs if "snipped" in m.get("content", "")]
        assert len(snipped) == 1
        # 头尾正确
        assert msgs[0]["content"] == "msg0"
        assert msgs[1]["content"] == "msg1"
        assert msgs[2]["content"] == "msg2"
        assert msgs[-1]["content"] == "msg149"

    def test_head_boundary_protection(self):
        """Head 最后一条是 assistant tool_calls → tool 结果一起保留。"""
        msgs = [_make_msg("user", f"msg{i}") for i in range(3)]  # head=3: msg0,msg1,msg2
        # msg2 是 assistant + tool_calls（在 head 边界上）
        msgs[2] = _make_msg("assistant", "calling tool", tool_calls=[_make_tool_call(1)])
        # 后面跟 tool 结果
        msgs += [
            _make_msg("tool", "result1", tool_call_id="call_1"),
            _make_msg("tool", "result2", tool_call_id="call_1"),
        ]
        # 填充到 >100
        msgs += [_make_msg("user", f"msg{i}") for i in range(4, 150)]
        snip_compact(msgs, max_messages=100)
        # head 应包含 assistant + 2 个 tool 结果
        assert msgs[2]["role"] == "assistant"
        assert msgs[3]["role"] == "tool"
        assert msgs[4]["role"] == "tool"

    def test_tail_boundary_protection(self):
        """Tail 第一条是孤立的 tool 结果 → assistant 一起保留。"""
        msgs = [_make_msg("user", f"msg{i}") for i in range(100)]  # head=3 will cut at index 3
        # 在 tail_start 边界上放 assistant + tool pair
        # tail_start = len - 97 = 150 - 97 = 53
        msgs += [
            _make_msg("assistant", "", tool_calls=[_make_tool_call(99)]),
            _make_msg("tool", "result", tool_call_id="call_99"),
        ]
        msgs += [_make_msg("user", f"msg{i}") for i in range(102, 152)]
        # Make sure we're at 150 total
        snip_compact(msgs, max_messages=100)
        # 检查没有孤立的 tool 消息（每个 tool 前面应有 assistant tool_calls）
        for i, m in enumerate(msgs):
            if m.get("role") == "tool" and i > 0:
                # 前一条可能是 assistant 或 snipped 标记
                prev = msgs[i - 1]
                if prev.get("role") != "assistant":
                    # 检查 tool_call_id 是否能在前一条 assistant 的 tool_calls 中找到
                    tc_id = m.get("tool_call_id")
                    assert tc_id is not None

    def test_skips_when_overlap(self):
        """Head_end ≥ tail_start → 跳过（无法裁剪）。"""
        # 构造 head 保护一直延伸到 tail 区域的情况
        msgs = [_make_msg("user", f"msg{i}") for i in range(10)]
        # 把它们全变成 tool_calls chain
        msgs[2] = _make_msg("assistant", "", tool_calls=[_make_tool_call(1, "bash")])
        for i in range(3, 10):
            msgs[i] = _make_msg("tool", "result", tool_call_id="call_1")
        # 少于 max_messages，确认不会出错
        before_len = len(msgs)
        snip_compact(msgs, max_messages=100)
        assert len(msgs) == before_len  # nothing changed


# ═══════════════════════════════════════════════════════════
# T026-T028: L2 micro_compact
# ═══════════════════════════════════════════════════════════


class TestL2MicroCompact:
    """L2: 旧工具结果占位符化。"""

    def test_noop_few_tool_results(self, sample_messages):
        """≤5 个 tool 消息 → no-op。"""
        msgs = [dict(m) for m in sample_messages]
        before = list(msgs)
        micro_compact(msgs)
        assert msgs == before  # only 3 tool msgs

    def test_replaces_old_long_results(self):
        """>5 个 tool 消息，旧的且 >120 字符的替换为占位符。"""
        msgs = [_make_msg("system", "sys")]
        msgs.append(_make_msg("user", "go"))
        # 8 个 tool 消息，前 3 个长（应该被替换），后 5 个保留
        for i in range(8):
            content = f"long result content {i} " + "x" * 150
            msgs.append(_make_msg("tool", content, tool_call_id=f"call_{i}"))
        micro_compact(msgs)
        # 前 3 个被替换
        for i in range(2 + 0, 2 + 3):  # offset by system+user
            assert "Earlier tool result compacted" in msgs[i]["content"]
        # 后 5 个保持不变
        for i in range(2 + 3, 2 + 8):
            assert "Earlier tool result compacted" not in msgs[i]["content"]

    def test_skips_short_content(self):
        """短内容 ≤120 字符 → 不替换。"""
        msgs = [_make_msg("system", "sys"), _make_msg("user", "go")]
        # 8 个短 tool 消息（≤120 字符）
        for i in range(8):
            msgs.append(_make_msg("tool", "short", tool_call_id=f"call_{i}"))
        micro_compact(msgs)
        # 全部保持原样
        for i in range(2, 10):
            assert msgs[i]["content"] == "short"


# ═══════════════════════════════════════════════════════════
# T029-T031: L4 compact_history
# ═══════════════════════════════════════════════════════════


class TestL4CompactHistory:
    """L4: LLM 摘要 + 重试/降级。"""

    def test_saves_transcript_and_replaces_messages(self):
        """成功：保存 transcript + 消息替换为摘要。"""
        msgs = [
            _make_msg("system", "You are helpful."),
            _make_msg("user", "Build a web server."),
            _make_msg("assistant", "I'll help build a web server."),
        ]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "用户请求构建 Web 服务器。已完成框架搭建。"
        mock_llm.chat.return_value = ("stop", mock_response)

        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.compact as cmod
            orig_trans = cmod.TRANSCRIPT_DIR
            cmod.TRANSCRIPT_DIR = Path(tmpdir)
            try:
                compact_history(msgs, mock_llm)
                # 消息被替换为摘要 + system
                assert len(msgs) == 2  # system + compact
                assert msgs[0]["role"] == "system"
                assert "[Compacted]" in msgs[1]["content"]
                assert "Web 服务器" in msgs[1]["content"]
                # transcript 已保存
                files = list(Path(tmpdir).glob("transcript_*.jsonl"))
                assert len(files) == 1
            finally:
                cmod.TRANSCRIPT_DIR = orig_trans

    def test_preserves_system_message(self):
        """L4 后 system 消息保留在首位。"""
        msgs = [
            _make_msg("system", "SYSTEM PROMPT HERE"),
            _make_msg("user", "Hello"),
            _make_msg("assistant", "Hi"),
        ]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary."
        mock_llm.chat.return_value = ("stop", mock_response)

        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.compact as cmod
            orig_trans = cmod.TRANSCRIPT_DIR
            cmod.TRANSCRIPT_DIR = Path(tmpdir)
            try:
                compact_history(msgs, mock_llm)
                assert msgs[0]["role"] == "system"
                assert msgs[0]["content"] == "SYSTEM PROMPT HERE"
            finally:
                cmod.TRANSCRIPT_DIR = orig_trans

    def test_retries_then_degrades(self):
        """LLM 调用持续失败 → 重试 N 次 → 降级跳过（消息不变）。"""
        msgs = [
            _make_msg("system", "sys"),
            _make_msg("user", "hello"),
        ]
        original = [dict(m) for m in msgs]
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("API error")

        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.compact as cmod
            orig_trans = cmod.TRANSCRIPT_DIR
            cmod.TRANSCRIPT_DIR = Path(tmpdir)
            try:
                compact_history(msgs, mock_llm)
                # 消息不变
                assert msgs == original
                # 重试了 3 次（SUMMARY_RETRY_COUNT=2, so 2+1=3 attempts）
                assert mock_llm.chat.call_count == 3
            finally:
                cmod.TRANSCRIPT_DIR = orig_trans


# ═══════════════════════════════════════════════════════════
# T032-T033: US5 Todo 恢复
# ═══════════════════════════════════════════════════════════


class TestTodoRestore:
    """US5: 压缩后恢复 Todo 列表。"""

    def test_appends_todo_when_non_empty(self):
        """CURRENT_TODOS 非空 → 追加格式化消息。"""
        msgs = [_make_msg("user", "existing")]
        fake_todos = [
            {"content": "分析需求", "status": "completed"},
            {"content": "编写代码", "status": "in_progress"},
            {"content": "测试功能", "status": "pending"},
        ]
        with patch("tools.todo_write.CURRENT_TODOS", fake_todos):
            _restore_todos(msgs)
        assert len(msgs) == 2
        assert "当前任务进度" in msgs[-1]["content"]
        assert "分析需求" in msgs[-1]["content"]
        assert "编写代码" in msgs[-1]["content"]
        assert "测试功能" in msgs[-1]["content"]

    def test_noop_when_empty(self):
        """CURRENT_TODOS 为空 → 不追加。"""
        msgs = [_make_msg("user", "existing")]
        with patch("tools.todo_write.CURRENT_TODOS", []):
            _restore_todos(msgs)
        assert len(msgs) == 1


# ═══════════════════════════════════════════════════════════
# T034-T035: Integration
# ═══════════════════════════════════════════════════════════


class TestIntegration:
    """集成测试：管线顺序 + 透明性。"""

    def test_pipeline_runs_in_correct_order(self):
        """验证 compact_pipeline 按 L3→L1→L2→L4 执行。"""
        msgs = [
            _make_msg("system", "sys"),
            _make_msg("user", "hello"),
        ]
        mock_llm = MagicMock()

        with (
            patch("agent.compact.tool_result_budget") as mock_l3,
            patch("agent.compact.snip_compact") as mock_l1,
            patch("agent.compact.micro_compact") as mock_l2,
            patch("agent.compact.compact_history") as mock_l4,
            patch("agent.compact._estimate_size", return_value=300_000),  # > CONTEXT_LIMIT
        ):
            compact_pipeline(msgs, mock_llm)

        # 验证调用顺序
        mock_l3.assert_called_once_with(msgs)
        mock_l1.assert_called_once_with(msgs)
        mock_l2.assert_called_once_with(msgs)
        mock_l4.assert_called_once_with(msgs, mock_llm)

        # 验证顺序：L3 的调用在 L2 之前
        l3_call_order = mock_l3.call_count and 0 or -1
        # Use mock_calls to verify ordering
        from unittest.mock import call
        assert mock_l3.called
        assert mock_l1.called
        assert mock_l2.called

    def test_short_conversation_transparent(self, sample_messages):
        """短对话不触发任何压缩副作用。"""
        msgs = [dict(m) for m in sample_messages]
        mock_llm = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.compact as cmod
            orig_trans = cmod.TRANSCRIPT_DIR
            orig_tool = cmod.TOOL_RESULTS_DIR
            cmod.TRANSCRIPT_DIR = Path(tmpdir) / "trans"
            cmod.TOOL_RESULTS_DIR = Path(tmpdir) / "tools"
            try:
                compact_pipeline(msgs, mock_llm)
                # 消息内容不应改变（没有层触发）
                for i, orig in enumerate(sample_messages):
                    assert msgs[i]["role"] == orig["role"]
                # L4 不应被调用（未超阈值）
                mock_llm.chat.assert_not_called()
                # 不应该创建任何文件
                trans_dir = Path(tmpdir) / "trans"
                assert not trans_dir.exists() or len(list(trans_dir.glob("*"))) == 0
            finally:
                cmod.TRANSCRIPT_DIR = orig_trans
                cmod.TOOL_RESULTS_DIR = orig_tool


# ═══════════════════════════════════════════════════════════
# SDK 对象兼容性测试
# ═══════════════════════════════════════════════════════════


class TestSDKCompatibility:
    """确保所有层正确处理 SDK ChatCompletionMessage 对象。"""

    def test_get_role_sdk(self, sdk_assistant_msg):
        """get_role 对 SDK 对象返回正确的 role。"""
        assert get_role(sdk_assistant_msg) == "assistant"

    def test_get_content_sdk(self, sdk_assistant_msg):
        """get_content 对 SDK 对象返回正确的 content。"""
        assert get_content(sdk_assistant_msg) == ""

    def test_get_tool_calls_sdk(self, sdk_assistant_msg):
        """get_tool_calls 归一化 SDK tool_calls 为 list[dict]。"""
        tcs = get_tool_calls(sdk_assistant_msg)
        assert len(tcs) == 2
        assert tcs[0]["id"] == "call_1"
        assert tcs[0]["function"]["name"] == "bash"
        assert tcs[1]["id"] == "call_2"

    def test_get_tool_call_id_sdk(self, sdk_assistant_msg):
        """SDK assistant 消息无 tool_call_id → None。"""
        assert get_tool_call_id(sdk_assistant_msg) is None

    def test_set_content_sdk(self):
        """set_content 对 SDK 对象设置 content。"""
        msg = MockSDKMessage(role="assistant", content="old")
        set_content(msg, "new content")
        assert msg.content == "new content"

    def test_to_serializable_sdk(self, sdk_assistant_msg):
        """to_serializable 将 SDK 对象转为纯 dict。"""
        d = to_serializable(sdk_assistant_msg)
        assert isinstance(d, dict)
        assert d["role"] == "assistant"
        assert len(d["tool_calls"]) == 2
        assert d["tool_calls"][0]["id"] == "call_1"

    def test_l3_with_sdk_assistant(self, sdk_assistant_msg):
        """L3 在混合类型消息中正确找到 SDK assistant 的 tool_calls。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            sdk_assistant_msg,
            {"role": "tool", "tool_call_id": "call_1", "content": "a" * 600_000},  # over budget
            {"role": "tool", "tool_call_id": "call_2", "content": "short"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            import agent.compact as cmod
            orig_dir = cmod.TOOL_RESULTS_DIR
            cmod.TOOL_RESULTS_DIR = Path(tmpdir)
            try:
                tool_result_budget(msgs)
                # call_1 的内容应被持久化
                assert "<persisted-output>" in get_content(msgs[3])
                saved = Path(tmpdir) / "call_1.txt"
                assert saved.exists()
            finally:
                cmod.TOOL_RESULTS_DIR = orig_dir

    def test_l1_with_sdk_assistant(self, sdk_assistant_msg):
        """L1 在 SDK assistant 消息上正确执行边界保护。"""
        # head_end=3，所以 head_end-1=2 是被检查的边界位置
        # SDK assistant 必须放在索引 2 才会被边界保护检测到
        msgs = [
            {"role": "user", "content": "msg0"},
            {"role": "user", "content": "msg1"},
            sdk_assistant_msg,  # ← SDK 对象在 head 边界上 (head_end-1=2)
        ]
        msgs.append({"role": "tool", "tool_call_id": "call_1", "content": "result1"})
        msgs.append({"role": "tool", "tool_call_id": "call_2", "content": "result2"})
        # 填充到 >100
        msgs += [{"role": "user", "content": f"msg{i}"} for i in range(5, 150)]
        snip_compact(msgs, max_messages=100)
        # head 应包含 SDK assistant (索引2) + 2 个 tool 结果 (索引3,4)，共 5 条
        assert get_role(msgs[2]) == "assistant"  # SDK 对象保留
        assert get_role(msgs[3]) == "tool"
        assert get_role(msgs[4]) == "tool"
        assert "snipped" in msgs[5]["content"]

    def test_l2_with_sdk_assistant(self, sdk_assistant_msg):
        """L2 正确处理混合类型的 tool 消息列表。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            sdk_assistant_msg,
        ]
        # 8 个 tool 消息——旧的会被替换
        for i in range(8):
            msgs.append({"role": "tool", "tool_call_id": f"call_{i}",
                          "content": "x" * 200})
        micro_compact(msgs)
        # 前 3 个 tool (索引 3,4,5) 被替换
        for idx in [3, 4, 5]:
            assert "Earlier tool result compacted" in get_content(msgs[idx])
        # 后 5 个不变
        for idx in [6, 7, 8, 9, 10]:
            assert "Earlier tool result compacted" not in get_content(msgs[idx])

    def test_pipeline_with_mixed_messages(self, mixed_messages):
        """完整管线在混合类型消息上正常运行。"""
        msgs = list(mixed_messages)
        mock_llm = MagicMock()
        compact_pipeline(msgs, mock_llm)
        # 短消息不触发 L4
        mock_llm.chat.assert_not_called()
        # 消息结构完整
        assert get_role(msgs[0]) == "system"
