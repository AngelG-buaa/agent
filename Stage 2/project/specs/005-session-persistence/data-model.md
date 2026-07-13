# Data Model: Session 持久化

**Feature**: 005-session-persistence
**Date**: 2026-07-13

## Schema (per-session SQLite database)

每个 session 对应一个独立的 SQLite 数据库文件 `{uuid}.db`，存放在 `.myagent/sessions/`。

### Table: sessions

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| id | TEXT | PRIMARY KEY | Session UUID (uuid4) |
| updated_at | TEXT | NOT NULL | ISO 8601 timestamp of last activity |
| title | TEXT | NOT NULL DEFAULT 'Untitled' | First user message truncated to 50 chars |
| message_count | INTEGER | NOT NULL DEFAULT 0 | Total messages in this session |

### Table: messages

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| id | TEXT | PRIMARY KEY | Message UUID |
| session_id | TEXT | NOT NULL | FK to sessions.id |
| seq | INTEGER | NOT NULL | Monotonic sequence number, 0-based |
| role | TEXT | NOT NULL | 'user' / 'assistant' / 'system' |
| content | TEXT | | Message body (nullable for tool_calls-only assistant msg) |
| tool_calls | TEXT | | JSON array of tool calls (assistant msg only) |
| tool_call_id | TEXT | | Tool call ID (tool msg only, links to assistant's tool_calls[].id) |

**Index**: `CREATE INDEX idx_messages_session_seq ON messages(session_id, seq)` — for fast ordered recovery

**3NF Verification**:
- `id → {session_id, seq, role, content, tool_calls, tool_call_id}` — no partial dependencies
- No non-prime attribute transitively determines another — `tool_name` intentionally excluded (recovered from tool_calls JSON at load time), avoiding `id → tool_call_id → tool_name` violation

### Table: permissions

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| session_id | TEXT | PRIMARY KEY (composite) | FK to sessions.id |
| tool_name | TEXT | PRIMARY KEY (composite) | Tool name allowed |

**Semantics**: Row exists = tool is allowed for this session. Row absent = must re-confirm.

### Table: todos

| Column | Type | Constraints | Description |
|--------|------|------------|-------------|
| session_id | TEXT | PRIMARY KEY (composite) | FK to sessions.id |
| content | TEXT | PRIMARY KEY (composite) | Todo item text |
| status | TEXT | NOT NULL | 'pending' / 'in_progress' / 'completed' |
| active_form | TEXT | NOT NULL | Present-tense label shown while in progress |

## Entity Relationship

```
sessions (1) ──── (N) messages
sessions (1) ──── (N) permissions
sessions (1) ──── (N) todos
```

All tables share `session_id` as foreign key. Since each session is its own .db file, `session_id` is technically redundant in child tables but retained for consistency and potential future migration to single-file mode.

## State Transitions

### Session Lifecycle

```
[Created] ──Conversation.start()──▶ [Active] ──exit──▶ [Ended]
                                      │
                                      ├── messages count > 0: .db retained
                                      └── messages count = 0: .db auto-deleted
```

### Todo Item Lifecycle

```
pending ──▶ in_progress ──▶ completed
   │                           │
   └───────────────────────────┘ (can jump directly)
```

## File Layout

```text
.myagent/
└── sessions/
    ├── a1b2c3d4-e5f6-7890-abcd-ef1234567890.db
    ├── b2c3d4e5-f6a7-8901-bcde-f12345678901.db
    └── ...
```
