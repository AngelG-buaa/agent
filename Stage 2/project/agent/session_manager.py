"""会话持久化的数据结构和 SQLite Repository。"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tooling.permission.engine import PermissionGrant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SessionSummary:
    """SessionSummary 是会话列表使用的轻量只读信息。"""
    id: str
    title: str
    updated_at: str
    message_count: int


@dataclass
class SessionSnapshot:
    """SessionSnapshot 是恢复会话所需的完整持久化快照。"""
    id: str
    title: str
    updated_at: str
    message_count: int
    messages: list[dict] = field(default_factory=list)
    permissions: list[PermissionGrant] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SessionError(Exception):
    """Session 创建、读取、写入或管理失败。"""


class SessionCorrupted(SessionError):
    """schema 不兼容或文件损坏。"""


# ---------------------------------------------------------------------------
# SQL Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL,
    message_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    UNIQUE(session_id, seq),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS permissions (
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    rule_content TEXT NOT NULL,
    PRIMARY KEY(session_id, tool_name, rule_content),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS todos (
    session_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    active_form TEXT NOT NULL,
    PRIMARY KEY(session_id, position),
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

"""


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """SessionManager 是无运行时会话状态的 SQLite Repository。

    它负责会话、消息、权限和 Todo 的持久化读写，
    不持有 ActiveSession，也不参与终端交互或 Agent 执行流程。
    """

    def __init__(self, sessions_dir: str):
        self.sessions_dir = os.path.realpath(sessions_dir)
        os.makedirs(self.sessions_dir, exist_ok=True)

    # ---- path helpers ----

    def _db_path(self, session_id: str) -> str:
        """校验 session_id 格式并返回安全的数据库路径。"""
        # 1. UUID 格式校验
        try:
            uuid.UUID(session_id)
        except (ValueError, TypeError):
            raise SessionError(f"无效的 session id: {session_id!r}")

        # 2. 路径越界检查
        resolved = os.path.realpath(
            os.path.join(self.sessions_dir, f"{session_id}.db")
        )
        if not resolved.startswith(os.path.realpath(self.sessions_dir)):
            raise SessionError(f"路径越界: {resolved}")

        return resolved

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(self, system_message: dict) -> str:
        """创建会话数据库，保存元数据和首条 system message。"""
        session_id = str(uuid.uuid4())
        db_path = self._db_path(session_id)

        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.executescript(SCHEMA)

                with conn:
                    now = _utcnow()
                    conn.execute(
                        "INSERT INTO sessions(id, updated_at, title, message_count) "
                        "VALUES (?, ?, ?, ?)",
                        (session_id, now, "Untitled", 1),
                    )
                    self._insert_message(
                        conn, _new_message_id(), session_id, seq=0, msg=system_message,
                    )
        except sqlite3.Error as exc:
            raise SessionError("创建 session 失败") from exc

        return session_id

    def load_session(self, session_id: str) -> SessionSnapshot:
        """在同一事务中读取恢复会话所需的完整快照。"""
        db_path = self._db_path(session_id)

        if not os.path.exists(db_path):
            raise SessionError(f"Session 文件不存在: {session_id}")

        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("BEGIN")

                with conn:
                    # 每个数据库只允许保存与文件名一致的一条 session metadata。
                    row = conn.execute(
                        "SELECT id, updated_at, title, message_count FROM sessions"
                    ).fetchone()
                    if row is None:
                        raise SessionCorrupted("sessions 表中无记录")
                    if row[0] != session_id:
                        raise SessionCorrupted(
                            f"Session 身份不一致: 文件={session_id}, 表内={row[0]}"
                        )

                    # messages
                    msg_rows = conn.execute(
                        "SELECT role, content, tool_calls, tool_call_id "
                        "FROM messages WHERE session_id = ? ORDER BY seq",
                        (session_id,),
                    ).fetchall()

                    # permissions
                    perm_rows = conn.execute(
                        "SELECT tool_name, rule_content FROM permissions "
                        "WHERE session_id = ?",
                        (session_id,),
                    ).fetchall()

                    # todos
                    todo_rows = conn.execute(
                        "SELECT content, status, active_form, position "
                        "FROM todos WHERE session_id = ? ORDER BY position",
                        (session_id,),
                    ).fetchall()

                # 反序列化
                try:
                    messages = _deserialize_messages(msg_rows)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise SessionCorrupted("消息 JSON 损坏") from exc
                from tooling.permission.engine import PermissionGrant
                permissions = [
                    PermissionGrant(tool_name=r[0], rule_content=r[1])
                    for r in perm_rows
                ]
                todos = [
                    {
                        "content": r[0], "status": r[1],
                        "active_form": r[2], "position": r[3],
                    }
                    for r in todo_rows
                ]

                return SessionSnapshot(
                    id=row[0],
                    updated_at=row[1],
                    title=row[2],
                    message_count=row[3],
                    messages=messages,
                    permissions=permissions,
                    todos=todos,
                )

        except SessionError:
            raise
        except sqlite3.Error as exc:
            raise SessionError(
                f"加载 session 失败: {session_id}"
            ) from exc

    def list_sessions(self) -> list[SessionSummary]:
        """按 updated_at DESC 排序，只读 metadata。

        损坏或 schema 不兼容的数据库跳过并打印警告。
        """
        if not os.path.isdir(self.sessions_dir):
            return []

        results: list[SessionSummary] = []
        for fname in sorted(os.listdir(self.sessions_dir)):
            if not fname.endswith(".db"):
                continue

            session_id = fname[:-3]  # strip .db
            db_path = os.path.join(self.sessions_dir, fname)

            # 跳过不存在的文件（可能在列出前被外部删除）
            if not os.path.isfile(db_path):
                continue

            try:
                uuid.UUID(session_id)
                with contextlib.closing(sqlite3.connect(db_path)) as conn:
                    conn.execute("PRAGMA foreign_keys = ON")
                    row = conn.execute(
                        "SELECT id, updated_at, title, message_count FROM sessions"
                    ).fetchone()
                    if row is None:
                        raise SessionCorrupted("sessions 表中无记录")
                    if row[0] != session_id:
                        raise SessionCorrupted(
                            f"Session 身份不一致: 文件={session_id}, 表内={row[0]}"
                        )

                    results.append(SessionSummary(
                        id=row[0],
                        updated_at=row[1],
                        title=row[2],
                        message_count=row[3],
                    ))
            except (ValueError, sqlite3.Error, SessionError):
                logger.warning("跳过损坏的 session 文件: %s", fname)

        results.sort(key=lambda s: s.updated_at, reverse=True)
        return results

    def delete_session(self, session_id: str) -> None:
        """物理删除 .db 文件。"""
        db_path = self._db_path(session_id)
        if not os.path.exists(db_path):
            raise SessionError(f"Session 文件不存在: {session_id}")
        try:
            os.remove(db_path)
        except OSError as exc:
            raise SessionError(
                f"删除 session 失败: {session_id}"
            ) from exc

    def rename_session(self, session_id: str, title: str) -> None:
        """UPDATE sessions SET title = ? WHERE id = ?"""
        db_path = self._db_path(session_id)
        if not os.path.exists(db_path):
            raise SessionError(f"Session 文件不存在: {session_id}")

        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                with conn:
                    cursor = conn.execute(
                        "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                        (title, _utcnow(), session_id),
                    )
                    if cursor.rowcount != 1:
                        raise SessionCorrupted(
                            f"Session metadata 缺失: {session_id}"
                        )
        except sqlite3.Error as exc:
            raise SessionError(
                f"重命名 session 失败: {session_id}"
            ) from exc

    # ------------------------------------------------------------------
    # 消息持久化
    # ------------------------------------------------------------------

    def append_message(self, session_id: str, message: dict) -> None:
        """按顺序追加一条消息，并更新会话标题、时间和消息数量。"""
        db_path = self._db_path(session_id)
        if not os.path.exists(db_path):
            raise SessionError(f"Session 文件不存在: {session_id}")

        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                with conn:
                    # BEGIN IMMEDIATE 防止并发 seq 冲突
                    conn.execute("BEGIN IMMEDIATE")

                    # 分配 seq
                    cursor = conn.execute(
                        "SELECT MAX(seq) FROM messages WHERE session_id = ?",
                        (session_id,),
                    )
                    max_seq = cursor.fetchone()[0]
                    seq = (max_seq + 1) if max_seq is not None else 0

                    # 插入消息
                    self._insert_message(
                        conn, _new_message_id(), session_id, seq, message,
                    )

                    # 更新 metadata
                    role = message.get("role", "")
                    if role == "user":
                        # 只在首条 user 消息时设置 title
                        existing = conn.execute(
                            "SELECT title, message_count FROM sessions WHERE id = ?",
                            (session_id,),
                        ).fetchone()
                        current_title = existing[0]
                        if current_title == "Untitled":
                            content = message.get("content", "")
                            if isinstance(content, str) and content.strip():
                                new_title = content.strip()[:50]
                                conn.execute(
                                    "UPDATE sessions SET title = ? WHERE id = ?",
                                    (new_title, session_id),
                                )

                    conn.execute(
                        "UPDATE sessions SET updated_at = ?, message_count = message_count + 1 "
                        "WHERE id = ?",
                        (_utcnow(), session_id),
                    )
        except SessionError:
            raise
        except sqlite3.Error as exc:
            raise SessionError(
                f"追加消息失败: session_id={session_id}"
            ) from exc

    # ------------------------------------------------------------------
    # 权限持久化
    # ------------------------------------------------------------------

    def save_grant(self, session_id: str, grant) -> None:
        """保存一条精确的会话权限授权；重复授权保持幂等。"""
        from tooling.permission.engine import PermissionGrant
        if not isinstance(grant, PermissionGrant):
            raise TypeError(f"期望 PermissionGrant，实际 {type(grant)}")

        db_path = self._db_path(session_id)
        if not os.path.exists(db_path):
            raise SessionError(f"Session 文件不存在: {session_id}")

        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO permissions(session_id, tool_name, rule_content) "
                        "VALUES (?, ?, ?)",
                        (session_id, grant.tool_name, grant.rule_content),
                    )
        except sqlite3.Error as exc:
            raise SessionError(
                f"保存权限 grant 失败: session_id={session_id}"
            ) from exc

    # ------------------------------------------------------------------
    # Todo 持久化
    # ------------------------------------------------------------------

    def save_todos(self, session_id: str, todos: list[dict]) -> None:
        """在同一事务中用当前 Todo 列表替换已保存状态。"""
        db_path = self._db_path(session_id)
        if not os.path.exists(db_path):
            raise SessionError(f"Session 文件不存在: {session_id}")

        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                with conn:
                    conn.execute(
                        "DELETE FROM todos WHERE session_id = ?",
                        (session_id,),
                    )
                    for position, todo in enumerate(todos):
                        conn.execute(
                            "INSERT INTO todos(session_id, position, content, status, active_form) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                session_id,
                                position,
                                todo.get("content", ""),
                                todo.get("status", "pending"),
                                todo.get("active_form", ""),
                            ),
                        )
        except sqlite3.Error as exc:
            raise SessionError(
                f"保存 Todo 失败: session_id={session_id}"
            ) from exc

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def cleanup_if_empty(self, session_id: str) -> None:
        """删除尚未产生用户消息的空会话数据库。"""
        db_path = self._db_path(session_id)
        if not os.path.exists(db_path):
            return

        try:
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE role = 'user'"
                ).fetchone()[0]
            if count == 0:
                os.remove(db_path)
        except (sqlite3.Error, OSError) as exc:
            raise SessionError(
                f"清理空 session 失败: {session_id}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _insert_message(
        conn: sqlite3.Connection,
        msg_id: str,
        session_id: str,
        seq: int,
        msg: dict,
    ) -> None:
        role = msg.get("role", "")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        # 序列化为 JSON 字符串
        content_str = (
            json.dumps(content, ensure_ascii=False)
            if content is not None and not isinstance(content, str)
            else content
        )
        tool_calls_str = (
            json.dumps(tool_calls, ensure_ascii=False, default=str)
            if tool_calls is not None
            else None
        )

        conn.execute(
            "INSERT INTO messages(id, session_id, seq, role, content, tool_calls, tool_call_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, seq, role, content_str, tool_calls_str, tool_call_id),
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """返回带时区的 UTC ISO-8601 时间戳。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _new_message_id() -> str:
    return str(uuid.uuid4())


def _deserialize_messages(rows: list[tuple]) -> list[dict]:
    """将数据库行反序列化为 OpenAI 兼容消息字典。

    恢复后的消息只含 role / content / tool_calls / tool_call_id 四个字段。
    tool_calls 的 function.arguments 保持 JSON 字符串（SDK 兼容格式）。
    """
    messages: list[dict] = []
    for row in rows:
        role, content_str, tool_calls_str, tool_call_id = row

        msg: dict = {"role": role}

        if content_str is not None:
            msg["content"] = content_str

        if tool_calls_str is not None:
            msg["tool_calls"] = json.loads(tool_calls_str)

        if tool_call_id is not None:
            msg["tool_call_id"] = tool_call_id

        messages.append(msg)

    return messages
