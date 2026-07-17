"""Integration tests for temporary Memory context and Conversation recall."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.agent import Agent, SubAgent, build_request_messages
from agent.conversation import Conversation
from agent.session_manager import SessionManager
from tooling.executor import ToolExecutor
from tooling.permission import PermissionEngine
from tools import register_all


def _executor():
    return ToolExecutor(
        permission_engine=PermissionEngine(default_behavior="allow"),
        approver=lambda name, params, reason: {"decision": "allow"},
    )


def _schema_names(executor: ToolExecutor) -> set[str]:
    return {schema["function"]["name"] for schema in executor.get_schemas()}


class TestRegisterAllMemoryWrite:
    def test_registers_memory_write_when_service_given(self):
        executor = _executor()

        register_all(
            executor,
            include_dangerous=False,
            workdir=".",
            memory_service=MagicMock(),
        )

        assert "memory_write" in _schema_names(executor)

    def test_omits_memory_write_by_default(self):
        executor = _executor()

        register_all(executor, include_dangerous=False, workdir=".")

        assert "memory_write" not in _schema_names(executor)


class TestTemporaryRequestContext:
    def test_builder_does_not_mutate_original_system_message(self):
        messages = [
            {"role": "system", "content": "Base prompt"},
            {"role": "user", "content": "Hello"},
        ]

        request = build_request_messages(messages, "<project_memory>fact</project_memory>")

        assert request is not messages
        assert "project_memory" in request[0]["content"]
        assert messages[0]["content"] == "Base prompt"

    def test_agent_sends_context_without_persisting_it(self):
        llm = MagicMock()
        response = SimpleNamespace(content="Done", tool_calls=None)
        llm.chat.return_value = ("stop", response)
        agent = Agent(llm, _executor())
        messages = [
            {"role": "system", "content": "Base prompt"},
            {"role": "user", "content": "Hello"},
        ]

        with patch("agent.agent.trigger_hooks", return_value=None):
            agent.run(messages, request_context="<project_memory>fact</project_memory>")

        request_messages = llm.chat.call_args.args[0]
        assert "project_memory" in request_messages[0]["content"]
        assert all("project_memory" not in str(message) for message in messages)

    def test_subagent_filters_memory_write(self):
        subagent = SubAgent(MagicMock(), _executor())
        assert "memory_write" in subagent.tool_filter


class _RecordingAgent:
    system_prompt = "System"

    def __init__(self):
        self.contexts = []

    def run(self, messages, on_message=None, request_context=None):
        self.contexts.append(request_context)
        if on_message:
            on_message({"role": "assistant", "content": "Done"})
        return "Done"


class _RecordingMemory:
    def __init__(self):
        self.queries = []

    def recall(self, query):
        self.queries.append(query)
        return SimpleNamespace(
            request_context="<project_memory>fact</project_memory>",
            warnings=(),
        )


class TestConversationRecall:
    def test_one_recall_per_user_turn_and_no_session_persistence(self, tmp_path):
        agent = _RecordingAgent()
        memory = _RecordingMemory()
        conversation = Conversation(
            agent,
            session_manager=SessionManager(str(tmp_path / "sessions")),
            permission_engine=PermissionEngine(default_behavior="allow"),
            system_message={"role": "system", "content": "System"},
            memory_service=memory,
        )
        conversation._controller.start_new()

        conversation._run_turn("Remember the architecture")

        assert memory.queries == ["Remember the architecture"]
        assert agent.contexts == ["<project_memory>fact</project_memory>"]
        assert all(
            "project_memory" not in str(message)
            for message in conversation._controller.active.messages
        )
        conversation._controller.close()
