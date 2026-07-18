"""终端 IO 层单元测试 —— IOBackend / OutputWriter / InputReader / ToolCallRenderer。"""
from terminal.io import (
    IOBackend,
    OutputWriter,
    InputReader,
    ToolCallRenderer,
    CaptureOutputWriter,
    FixedInputReader,
    TerminalOutputWriter,
    TerminalInputReader,
    DefaultToolCallRenderer,
)


class TestCaptureOutputWriter:
    """CaptureOutputWriter 基础功能（T016）。"""

    def test_write_appends_lines(self):
        cap = CaptureOutputWriter()
        cap.write("hello")
        cap.write("world")
        assert cap.lines == ["hello", "world"]

    def test_lines_returns_copy(self):
        cap = CaptureOutputWriter()
        cap.write("test")
        lines = cap.lines
        lines.append("mutated")
        assert cap.lines == ["test"]  # 原件不应受影响

    def test_write_empty_string(self):
        cap = CaptureOutputWriter()
        cap.write("")
        assert cap.lines == [""]

    def test_clear_empties_lines(self):
        cap = CaptureOutputWriter()
        cap.write("a")
        cap.write("b")
        cap.clear()
        assert cap.lines == []

    def test_clear_is_idempotent(self):
        cap = CaptureOutputWriter()
        cap.clear()
        assert cap.lines == []


class TestFixedInputReader:
    """FixedInputReader 基础功能（T016）。"""

    def test_read_returns_preset_answers(self):
        reader = FixedInputReader(["y", "n"])
        assert reader.read("") == "y"
        assert reader.read("") == "n"

    def test_remaining_tracks_unread(self):
        reader = FixedInputReader(["a", "b", "c"])
        assert reader.remaining == ["a", "b", "c"]
        reader.read("")
        assert reader.remaining == ["b", "c"]

    def test_exhausted_raises_eof_error(self):
        reader = FixedInputReader(["only"])
        reader.read("")
        try:
            reader.read("")
            assert False, "should have raised EOFError"
        except EOFError:
            pass

    def test_empty_list_immediately_exhausted(self):
        reader = FixedInputReader([])
        try:
            reader.read("")
            assert False, "should have raised EOFError"
        except EOFError:
            pass


class TestSemanticMethods:
    """OutputWriter 语义方法 info/warn/error/success（T016）。"""

    def test_info_defaults_to_write(self):
        cap = CaptureOutputWriter()
        cap.info("info msg")
        assert "info msg" in cap.lines[0]

    def test_warn_includes_emoji(self):
        cap = CaptureOutputWriter()
        cap.warn("warning")
        assert "⚠️" in cap.lines[0]

    def test_error_includes_emoji(self):
        cap = CaptureOutputWriter()
        cap.error("error")
        assert "❌" in cap.lines[0]

    def test_success_includes_emoji(self):
        cap = CaptureOutputWriter()
        cap.success("success")
        assert "✅" in cap.lines[0]

    def test_semantic_methods_all_captured(self):
        cap = CaptureOutputWriter()
        cap.info("a")
        cap.warn("b")
        cap.error("c")
        cap.success("d")
        assert len(cap.lines) == 4


class TestIOBackend:
    """IOBackend 容器和工厂方法（T016）。"""

    def test_default_constructs_all_fields(self):
        io = IOBackend()
        assert isinstance(io.output, TerminalOutputWriter)
        assert isinstance(io.input, TerminalInputReader)
        assert isinstance(io.tool_renderer, DefaultToolCallRenderer)

    def test_terminal_factory(self):
        io = IOBackend.terminal()
        assert isinstance(io.output, TerminalOutputWriter)
        assert isinstance(io.input, TerminalInputReader)
        assert isinstance(io.tool_renderer, DefaultToolCallRenderer)

    def test_custom_components(self):
        cap = CaptureOutputWriter()
        reader = FixedInputReader(["y"])
        io = IOBackend(output=cap, input=reader)
        assert io.output is cap
        assert io.input is reader


class TestDefaultToolCallRenderer:
    """DefaultToolCallRenderer 基础功能（T016）。"""

    def test_on_tool_call_writes_to_output(self):
        cap = CaptureOutputWriter()
        renderer = DefaultToolCallRenderer(cap)
        renderer.on_tool_call("read_file", {"path": "test.py"})
        assert "read_file" in cap.lines[0]

    def test_on_tool_result_does_nothing(self):
        cap = CaptureOutputWriter()
        renderer = DefaultToolCallRenderer(cap)
        renderer.on_tool_result("bash", {"stdout": "ok"})
        assert cap.lines == []


class TestAgentIOBackend:
    """Agent 注入 CaptureOutputWriter 后捕获输出（T017）。"""

    def _make_agent(self, io_backend=None):
        """创建一个最小 Agent 实例（跳过 LLM/Executor 构造）。"""
        from agent.agent import Agent
        llm = type("FakeLLM", (), {"chat": lambda self, m, t: ("stop", type("Msg", (), {"content": "", "tool_calls": None})())})()
        executor = type("FakeExec", (), {"get_schemas": lambda self: [], "execute": lambda self, n, p: {}})()
        return Agent(llm, executor, io_backend=io_backend)

    def test_agent_uses_io_backend(self):
        cap = CaptureOutputWriter()
        io = IOBackend(output=cap)
        agent = self._make_agent(io_backend=io)
        assert agent._io is io

    def test_agent_defaults_to_io_backend(self):
        agent = self._make_agent()
        assert isinstance(agent._io, IOBackend)


class TestTerminalApproverIO:
    """TerminalApprover 注入 FixedInputReader 后模拟交互（T018）。"""

    def test_fixed_reader_approves(self):
        from tooling.executor import TerminalApprover
        reader = FixedInputReader(["y"])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        result = approver("bash", {"command": "ls"}, None)
        assert result["decision"] == "allow"

    def test_fixed_reader_denies(self):
        from tooling.executor import TerminalApprover
        # deny (需要 2 次 read: 选 n → fallthrough → 输入拒绝原因/回车)
        reader = FixedInputReader(["n", ""])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        result = approver("bash", {"command": "ls"}, None)
        assert result["decision"] == "deny"

    def test_fixed_reader_session(self):
        from tooling.executor import TerminalApprover
        reader = FixedInputReader(["a"])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        result = approver("bash", {"command": "ls"}, None)
        assert result["decision"] == "session"

    def test_fixed_reader_multiple_calls(self):
        from tooling.executor import TerminalApprover
        # y→allow(1), n→deny+回车(2), a→session(1) = 4 次 read
        reader = FixedInputReader(["y", "n", "", "a"])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        assert approver("bash", {}, None)["decision"] == "allow"
        assert approver("bash", {}, None)["decision"] == "deny"
        assert approver("bash", {}, None)["decision"] == "session"

    def test_terminal_approver_strips_whitespace(self):
        from tooling.executor import TerminalApprover
        reader = FixedInputReader(["  y  "])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        result = approver("bash", {}, None)
        assert result["decision"] == "allow"

    def test_deny_with_reason(self):
        from tooling.executor import TerminalApprover
        reader = FixedInputReader(["n", "不安全"])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        result = approver("bash", {"command": "rm -rf /"}, None)
        assert result["decision"] == "deny"
        assert "不安全" in result.get("reason", "")

    def test_fixed_reader_session_tool(self):
        """输入 't' → decision == 'session_tool'。"""
        from tooling.executor import TerminalApprover
        reader = FixedInputReader(["t"])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        result = approver("bash", {"command": "rm file.txt"}, "确认删除")
        assert result["decision"] == "session_tool"

    def test_fixed_reader_session_tool_aliases(self):
        """'tool' 和 'trust' 都映射到 'session_tool'。"""
        from tooling.executor import TerminalApprover
        reader = FixedInputReader(["tool", "trust"])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        r1 = approver("bash", {}, None)
        r2 = approver("bash", {}, None)
        assert r1["decision"] == "session_tool"
        assert r2["decision"] == "session_tool"

    def test_multiple_calls_includes_t(self):
        """序列 y→allow, n→deny, a→session, t→session_tool。"""
        from tooling.executor import TerminalApprover
        reader = FixedInputReader(["y", "n", "", "a", "t"])
        approver = TerminalApprover(input_reader=reader, output=CaptureOutputWriter())
        assert approver("bash", {}, None)["decision"] == "allow"
        assert approver("bash", {}, None)["decision"] == "deny"
        assert approver("bash", {}, None)["decision"] == "session"
        assert approver("bash", {}, None)["decision"] == "session_tool"
