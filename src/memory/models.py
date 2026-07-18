"""Data contracts for durable Memory records and recall results."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_name(name: str) -> None:
    """Raise ValueError unless name is lowercase ASCII kebab-case."""
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise ValueError("name must be lowercase ASCII kebab-case")


def validate_description(description: str) -> None:
    """Raise ValueError unless description is one non-empty line."""
    if (
        not isinstance(description, str)
        or not description.strip()
        or "\n" in description
    ):
        raise ValueError("description must be one non-empty line")


def validate_body(body: str) -> None:
    """Raise ValueError unless body is non-empty."""
    if not isinstance(body, str) or not body.strip():
        raise ValueError("body cannot be empty")


class MemoryType(str, Enum):
    """Supported durable Memory categories."""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass(frozen=True)
class MemoryRecord:
    """One complete persisted Memory record."""

    name: str
    memory_type: MemoryType
    description: str
    body: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MemoryChange:
    """One explicit add or full-replacement update request."""

    action: Literal["add", "update"]
    name: str
    memory_type: MemoryType
    description: str
    body: str


@dataclass(frozen=True)
class MemoryMatch:
    """A recalled record with channel and fusion scores."""

    record: MemoryRecord
    semantic_score: float | None
    lexical_score: float | None
    rrf_score: float


@dataclass(frozen=True)
class MemoryRecall:
    """Recall result prepared for one Agent request."""

    matches: tuple[MemoryMatch, ...]
    request_context: str | None
    warnings: tuple[str, ...]
