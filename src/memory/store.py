"""Markdown persistence for project-level Memory records."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from memory.models import (
    MemoryRecord,
    MemoryType,
    validate_body,
    validate_description,
    validate_name,
)

_FRONTMATTER_FIELDS = {
    "name",
    "memory_type",
    "description",
    "created_at",
    "updated_at",
}


class MemoryStore:
    """Persist Memory records as independently readable Markdown files."""

    def __init__(self, memory_dir: str | Path):
        self.memory_dir = Path(memory_dir)
        self.items_dir = self.memory_dir / "items"
        self.index_path = self.memory_dir / "MEMORY.md"

    def list_records(self) -> list[MemoryRecord]:
        """Return all records ordered by their stable names."""
        self._ensure_directories()
        return [self._read(path) for path in sorted(self.items_dir.glob("*.md"))]

    def get(self, name: str) -> MemoryRecord:
        """Return one record, raising KeyError when its name does not exist."""
        self._validate_name(name)
        path = self.items_dir / f"{name}.md"
        if not path.is_file():
            raise KeyError(f"Memory does not exist: {name}")
        return self._read(path)

    def add(self, record: MemoryRecord) -> None:
        """Persist a new record and reject an existing name."""
        self._ensure_directories()
        self._validate_record(record)
        path = self.items_dir / f"{record.name}.md"
        if path.exists():
            raise FileExistsError(f"Memory already exists: {record.name}")
        self._atomic_write(path, self._serialize(record))
        self.rebuild_index()

    def update(self, record: MemoryRecord) -> None:
        """Replace an existing record and reject a missing name."""
        self._ensure_directories()
        self._validate_record(record)
        path = self.items_dir / f"{record.name}.md"
        if not path.is_file():
            raise KeyError(f"Memory does not exist: {record.name}")
        self._atomic_write(path, self._serialize(record))
        self.rebuild_index()

    def rebuild_index(self) -> None:
        """Rebuild the human-readable index from item files."""
        records = self.list_records()
        lines = [
            "# Project Memory",
            "",
            "This index is generated from `items/*.md`.",
            "",
        ]
        if records:
            for record in records:
                lines.append(
                    f"- **{record.name}** (`{record.memory_type.value}`): "
                    f"{record.description}"
                )
        else:
            lines.append("No memories stored.")
        lines.append("")
        self._atomic_write(self.index_path, "\n".join(lines))

    def _ensure_directories(self) -> None:
        self.items_dir.mkdir(parents=True, exist_ok=True)

    def _read(self, path: Path) -> MemoryRecord:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError(f"Invalid Memory frontmatter: {path}")

        frontmatter, separator, body = text[4:].partition("\n---\n")
        if not separator:
            raise ValueError(f"Invalid Memory frontmatter terminator: {path}")
        if body.startswith("\n"):
            body = body[1:]
        if body.endswith("\n"):
            body = body[:-1]

        try:
            metadata = json.loads(frontmatter)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid Memory JSON frontmatter: {path}") from exc

        if not isinstance(metadata, dict) or set(metadata) != _FRONTMATTER_FIELDS:
            raise ValueError(f"Unexpected Memory frontmatter fields: {path}")

        try:
            record = MemoryRecord(
                name=metadata["name"],
                memory_type=MemoryType(metadata["memory_type"]),
                description=metadata["description"],
                body=body,
                created_at=metadata["created_at"],
                updated_at=metadata["updated_at"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Memory record: {path}") from exc

        if path.stem != record.name:
            raise ValueError(
                f"Memory filename and name differ: {path.stem} != {record.name}"
            )
        self._validate_record(record)
        return record

    def _serialize(self, record: MemoryRecord) -> str:
        metadata = {
            "name": record.name,
            "memory_type": record.memory_type.value,
            "description": record.description,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        frontmatter = json.dumps(metadata, ensure_ascii=False, indent=2)
        return f"---\n{frontmatter}\n---\n\n{record.body}\n"

    def _validate_record(self, record: MemoryRecord) -> None:
        self._validate_name(record.name)
        if not isinstance(record.memory_type, MemoryType):
            raise ValueError("memory_type must be a MemoryType")
        validate_description(record.description)
        validate_body(record.body)

        created_at = self._parse_utc(record.created_at, "created_at")
        updated_at = self._parse_utc(record.updated_at, "updated_at")
        if updated_at < created_at:
            raise ValueError("updated_at cannot be earlier than created_at")

    @staticmethod
    def _validate_name(name: str) -> None:
        validate_name(name)

    @staticmethod
    def _parse_utc(value: str, field: str) -> datetime:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be an ISO-8601 string")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be valid ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError(f"{field} must use UTC timezone")
        return parsed

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
