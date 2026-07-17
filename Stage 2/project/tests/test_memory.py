"""Unit tests for Memory persistence, retrieval, service, and Tool."""

from types import SimpleNamespace

import numpy as np
import pytest

from memory.models import MemoryChange, MemoryMatch, MemoryRecord, MemoryType
from memory.retriever import MemoryRetriever
from memory.service import MemoryService
from memory.store import MemoryStore
from tools.memory_write import MemoryWriteTool

_TIME = "2026-07-16T08:00:00+00:00"


def _config(tmp_path, **overrides):
    values = {
        "memory_dir": str(tmp_path / "memory"),
        "semantic_threshold": 0.8,
        "lexical_threshold": 0.5,
        "recall_top_k": 3,
        "rrf_k": 60,
        "max_context_chars": 12_000,
        "max_record_chars": 4_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _record(
    name: str,
    body: str,
    *,
    memory_type: MemoryType = MemoryType.PROJECT,
    description: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        name=name,
        memory_type=memory_type,
        description=description or f"Description for {name}",
        body=body,
        created_at=_TIME,
        updated_at=_TIME,
    )


class _KeywordEmbedder:
    def encode_documents(self, texts):
        return np.array(
            [[1.0, 0.0] if "vector-target" in text else [0.0, 1.0] for text in texts],
            dtype=np.float32,
        )

    def encode_query(self, text):
        return np.array([1.0, 0.0], dtype=np.float32)


class _FailingEmbedder:
    def encode_documents(self, texts):
        raise RuntimeError("embedding offline")

    def encode_query(self, text):
        raise RuntimeError("embedding offline")


class TestMemoryStore:
    def test_add_and_markdown_round_trip(self, tmp_path):
        store = MemoryStore(tmp_path / "memory")
        record = _record(
            "architecture-rule",
            "Keep the Agent loop simple.",
            memory_type=MemoryType.FEEDBACK,
        )

        store.add(record)

        assert store.get(record.name) == record
        assert store.list_records() == [record]
        assert (tmp_path / "memory" / "items" / "architecture-rule.md").is_file()
        assert "architecture-rule" in (
            tmp_path / "memory" / "MEMORY.md"
        ).read_text(encoding="utf-8")

    def test_add_rejects_existing_name(self, tmp_path):
        store = MemoryStore(tmp_path / "memory")
        record = _record("stable-name", "Original body")
        store.add(record)

        with pytest.raises(FileExistsError):
            store.add(_record("stable-name", "Replacement body"))

        assert store.get("stable-name").body == "Original body"

    def test_update_requires_existing_name(self, tmp_path):
        store = MemoryStore(tmp_path / "memory")

        with pytest.raises(KeyError):
            store.update(_record("missing-name", "Body"))

    def test_update_fully_replaces_mutable_fields(self, tmp_path):
        store = MemoryStore(tmp_path / "memory")
        store.add(_record("stable-name", "Original body"))
        replacement = MemoryRecord(
            name="stable-name",
            memory_type=MemoryType.REFERENCE,
            description="New description",
            body="New body",
            created_at=_TIME,
            updated_at="2026-07-16T09:00:00+00:00",
        )

        store.update(replacement)

        assert store.get("stable-name") == replacement


class TestMemoryRetriever:
    def test_hybrid_candidates_are_unioned_and_rrf_ranked(self, tmp_path):
        config = _config(tmp_path)
        retriever = MemoryRetriever(_KeywordEmbedder(), config)
        records = [
            _record("a-semantic", "vector-target with unrelated wording"),
            _record("b-lexical", "remember deployment settings"),
        ]

        matches, warnings = retriever.retrieve("remember deployment", records)

        assert {match.record.name for match in matches} == {
            "a-semantic",
            "b-lexical",
        }
        by_name = {match.record.name: match for match in matches}
        assert by_name["a-semantic"].semantic_score == pytest.approx(1.0)
        assert by_name["a-semantic"].lexical_score is None
        assert by_name["b-lexical"].semantic_score is None
        assert by_name["b-lexical"].lexical_score == pytest.approx(1.0)
        assert warnings == ()

    def test_embedding_failure_degrades_to_lexical(self, tmp_path):
        retriever = MemoryRetriever(_FailingEmbedder(), _config(tmp_path))
        record = _record("lexical-memory", "remember deployment settings")

        matches, warnings = retriever.retrieve("remember deployment", [record])

        assert [match.record.name for match in matches] == ["lexical-memory"]
        assert matches[0].semantic_score is None
        assert warnings and "embedding offline" in warnings[0]

    def test_recall_is_limited_to_top_three(self, tmp_path):
        config = _config(
            tmp_path,
            semantic_threshold=0.5,
            lexical_threshold=2.0,
        )
        retriever = MemoryRetriever(_KeywordEmbedder(), config)
        records = [
            _record(f"vector-target-{index}", "vector-target")
            for index in range(4)
        ]

        matches, _ = retriever.retrieve("anything", records)

        assert len(matches) == 3


class _StaticRetriever:
    def __init__(self, matches=(), warnings=()):
        self.matches = list(matches)
        self.warnings = tuple(warnings)

    def retrieve(self, query, records):
        return self.matches, self.warnings


class TestMemoryService:
    def test_add_then_full_update_preserves_created_at(self, tmp_path):
        config = _config(tmp_path)
        store = MemoryStore(config.memory_dir)
        service = MemoryService(store, _StaticRetriever(), config)
        added = service.apply_change(
            MemoryChange(
                action="add",
                name="answer-style",
                memory_type=MemoryType.USER,
                description="Preferred answer style",
                body="Use concise answers.",
            )
        )

        updated = service.apply_change(
            MemoryChange(
                action="update",
                name="answer-style",
                memory_type=MemoryType.FEEDBACK,
                description="Updated answer style",
                body="Use concise answers with evidence.",
            )
        )

        assert updated.created_at == added.created_at
        assert updated.memory_type is MemoryType.FEEDBACK
        assert updated.description == "Updated answer style"
        assert updated.body == "Use concise answers with evidence."

    def test_add_and_update_have_explicit_existence_rules(self, tmp_path):
        config = _config(tmp_path)
        store = MemoryStore(config.memory_dir)
        service = MemoryService(store, _StaticRetriever(), config)
        change = MemoryChange(
            action="add",
            name="stable-memory",
            memory_type=MemoryType.PROJECT,
            description="Stable project fact",
            body="The fact remains valid.",
        )
        service.apply_change(change)

        with pytest.raises(FileExistsError):
            service.apply_change(change)
        with pytest.raises(KeyError):
            service.apply_change(
                MemoryChange(
                    action="update",
                    name="missing-memory",
                    memory_type=MemoryType.PROJECT,
                    description="Missing project fact",
                    body="Complete body.",
                )
            )

    def test_record_character_budget_includes_description(self, tmp_path):
        config = _config(tmp_path, max_record_chars=20)
        service = MemoryService(
            MemoryStore(config.memory_dir),
            _StaticRetriever(),
            config,
        )

        with pytest.raises(ValueError, match="exceeds 20"):
            service.apply_change(
                MemoryChange(
                    action="add",
                    name="oversized-memory",
                    memory_type=MemoryType.PROJECT,
                    description="Long description",
                    body="Complete body",
                )
            )

    def test_recall_builds_bounded_complete_context(self, tmp_path):
        first = _record("first-memory", "First complete body")
        second = _record("second-memory", "Second complete body")
        matches = [
            MemoryMatch(first, 0.9, 1.0, 0.03),
            MemoryMatch(second, 0.8, 1.0, 0.02),
        ]
        config = _config(tmp_path, max_context_chars=310)
        service = MemoryService(
            MemoryStore(config.memory_dir),
            _StaticRetriever(matches),
            config,
        )

        recall = service.recall("query")

        assert recall.request_context is not None
        assert "First complete body" in recall.request_context
        assert "Second complete body" not in recall.request_context
        assert recall.request_context.endswith("</project_memory>")
        assert len(recall.request_context) <= config.max_context_chars


class TestMemoryWriteTool:
    def test_add_returns_stable_result(self, tmp_path):
        config = _config(tmp_path)
        service = MemoryService(
            MemoryStore(config.memory_dir),
            _StaticRetriever(),
            config,
        )
        tool = MemoryWriteTool(service)

        result = tool.run(
            {
                "action": "add",
                "name": "project-language",
                "memory_type": "project",
                "description": "Project language",
                "body": "The project uses Python.",
            }
        )

        assert result == {"result": "memory_added", "name": "project-language"}

    def test_invalid_input_returns_error(self, tmp_path):
        config = _config(tmp_path)
        tool = MemoryWriteTool(
            MemoryService(
                MemoryStore(config.memory_dir),
                _StaticRetriever(),
                config,
            )
        )

        result = tool.run(
            {
                "action": "add",
                "name": "Invalid Name",
                "memory_type": "project",
                "description": "Invalid name",
                "body": "Body",
            }
        )

        assert "error" in result
