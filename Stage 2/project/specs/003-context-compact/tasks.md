# Tasks: Context Compact（上下文压缩）

**Input**: Design documents from `specs/003-context-compact/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md

**Tests**: Per constitution Principle VII, the compact module is part of the Agent core layer and requires unit tests.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

Project root: `d:\LLM\Agent\Stage 2\project\`

```text
agent/compact.py        # NEW: all compaction logic
agent/agent.py          # MODIFY: +1 line in run()
config.py               # MODIFY: +CompactionConfig
tests/test_compact.py   # NEW: unit tests
```

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Configuration and module scaffolding

- [X] T001 Create `CompactionConfig` dataclass with all 11 fields and defaults in [config.py](config.py)
- [ ] T002 [P] Create `agent/compact.py` with module docstring, constants (CONTEXT_LIMIT, MAX_MESSAGES_SNIP, KEEP_RECENT, etc.), and directory path helpers (`TRANSCRIPT_DIR`, `TOOL_RESULTS_DIR`, `_ensure_dir()`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared helpers, orchestrator skeleton, and Agent loop integration point. All user story implementation depends on this phase.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Implement `_estimate_size(messages)` — character-count approximation in [agent/compact.py](agent/compact.py)
- [ ] T004 [P] Implement OpenAI-format message helper functions in [agent/compact.py](agent/compact.py): `_has_tool_calls(msg)` (assistant with tool_calls), `_is_tool_for_ids(msg, tc_ids)` (tool result matching given IDs), `_has_tool_call_id(msg, tc_id)` (assistant containing specific tool_call_id)
- [ ] T005 Implement `compact_pipeline(messages, llm)` orchestrator skeleton in [agent/compact.py](agent/compact.py) — calls L3→L1→L2→L4 in order, with `print("[auto compact]")` only before L4. Each layer function starts as a no-op stub (commented-out or `pass`), to be filled in subsequent phases.
- [ ] T006 Inject `compact_pipeline(messages, self.llm)` call in [agent/agent.py](agent/agent.py) `Agent.run()` loop — after `PreLLMCall` hook block, before `get_schemas()` call. Add `from agent.compact import compact_pipeline` import.

**Checkpoint**: Module skeleton wired in, Agent loop calls compact_pipeline (no-op). Agent runs as before.

---

## Phase 3: User Story 1 — L3 超大工具输出自动降级 (Priority: P1) 🎯 MVP

**Goal**: When a single tool result exceeds 30KB and the latest round totals >500KB, persist the output to disk and replace with a preview + file path pointer in the message.

**Independent Test**: Execute a bash command returning 80KB output, verify the content is written to `.task_outputs/tool-results/<tool_call_id>.txt` and the message content is replaced with `<persisted-output>` block containing file path and 2000-char preview.

### Implementation for User Story 1

- [ ] T007 [US1] Implement `tool_result_budget(messages)` in [agent/compact.py](agent/compact.py):
  - Find the last `role="tool"` message in the list
  - If its content size ≤ `TOOL_RESULT_BUDGET_BYTES` (500KB) → return
  - If its content size ≤ `PERSIST_THRESHOLD` (30KB) → return
  - Create `.task_outputs/tool-results/` directory if needed
  - Write full content to `.task_outputs/tool-results/<tool_call_id>.txt`
  - Replace `msg["content"]` with `<persisted-output>\nFull output: <path>\nPreview:\n<first 2000 chars>\n</persisted-output>`
- [ ] T008 [US1] Verify L3 stub is replaced with real implementation in `compact_pipeline()` — ensure L3 runs before L2 in the orchestrator

**Checkpoint**: Large tool outputs automatically persisted to disk. Agent continues working without context overflow from a single big result.

---

## Phase 4: User Story 2 — L1 长对话自动截断 (Priority: P1)

**Goal**: When message count exceeds 100, keep first 3 + last 97 messages, replace middle with a `[snipped N messages]` placeholder. Protect tool_use/tool_result pair integrity at cut boundaries.

**Independent Test**: Construct 150 messages with tool_use/tool_result pairs near the cut boundaries, verify output has ≤101 messages with no broken pairs.

### Implementation for User Story 1

- [ ] T009 [US2] Implement `snip_compact(messages, max_messages=100)` in [agent/compact.py](agent/compact.py):
  - If `len(messages) <= max_messages` → return
  - Set `head_end = 3`, `tail_start = len(messages) - 97`
  - **Boundary protection 1 (head)**: If `messages[head_end-1]` is assistant with tool_calls, scan forward and include all tool messages matching those tool_call_ids
  - **Boundary protection 2 (tail)**: If `messages[tail_start]` is a tool message, check if `messages[tail_start-1]` is the assistant that called it; if so, move `tail_start` back by 1
  - If `head_end >= tail_start` → return (protection caused overlap, nothing to cut)
  - Replace messages with `messages[:head_end] + [{"role": "user", "content": f"[snipped {N} messages]"}] + messages[tail_start:]`
- [ ] T010 [US2] Verify L1 stub is replaced with real implementation in `compact_pipeline()`

**Checkpoint**: Long conversations automatically trimmed without breaking tool call pairing.

---

## Phase 5: User Story 3 — L2 旧工具结果占位符化 (Priority: P2)

**Goal**: When there are >5 tool_result messages, keep the latest 5 intact, replace older ones (with content >120 chars) with a short placeholder.

**Independent Test**: Construct 10 tool_result messages (7 long, 3 short), verify only latest 5 long ones survive, older long ones become placeholder, short ones unchanged.

### Implementation for User Story 3

- [ ] T011 [US3] Implement `micro_compact(messages)` in [agent/compact.py](agent/compact.py):
  - Collect all indices where `messages[i]["role"] == "tool"`
  - If `len(indices) <= KEEP_RECENT` (5) → return
  - For each index in `indices[:-KEEP_RECENT]`:
    - If `len(messages[idx]["content"]) > MIN_CONTENT_LENGTH` (120):
      - Replace with `"[Earlier tool result compacted. Re-run if needed.]"`
- [ ] T012 [US3] Verify L2 stub is replaced with real implementation in `compact_pipeline()`

**Checkpoint**: Old tool results automatically compressed to placeholders.

---

## Phase 6: User Story 4 + 5 — L4 LLM 摘要 + Todo 恢复 (Priority: P2)

**Goal (US4)**: When L1-L3 complete and total message size still exceeds 200K chars, save transcript to `.transcripts/`, call LLM to generate a summary preserving 5 key dimensions, replace all messages with the summary (keeping system message). Retry up to 2 times on failure; skip compaction on total failure.

**Goal (US5)**: After L4 compaction, if CURRENT_TODOS is non-empty, append a formatted todo list message to restore task progress awareness.

**Independent Test**: Construct >200K char messages, trigger L4, verify transcript saved, messages replaced with summary + system + optional todo recovery. Simulate API failure to verify retry+degrade.

### Implementation for User Story 4

- [ ] T013 [US4] Implement `compact_history(messages, llm)` in [agent/compact.py](agent/compact.py):
  - Save full transcript to `.transcripts/transcript_<timestamp>.jsonl` (one JSON message per line)
  - Build summary prompt (Chinese, 5 dimensions: goal, findings, files, remaining work, user constraints)
  - Truncate conversation input to `SUMMARY_INPUT_CAP` (80K chars) if needed
  - Call `llm.chat()` with `tools=[]` for a plain-text summary response
  - Retry up to `SUMMARY_RETRY_COUNT` (2) times on failure; return without changes on total failure
  - On success: preserve system message (if `messages[0]["role"] == "system"`), replace all other messages with `[Compacted]\n\n<summary>`
- [ ] T014 [US4] Verify L4 condition is wired in `compact_pipeline()` — only triggers when `_estimate_size(messages) > CONTEXT_LIMIT`

### Implementation for User Story 5

- [ ] T015 [US5] Implement `_restore_todos(messages)` in [agent/compact.py](agent/compact.py):
  - Import `CURRENT_TODOS` from `tools.todo_write`
  - If list is empty → return
  - Format each todo as `- [icon] content` (icons: ✓=completed, ▸=in_progress, =pending)
  - Append formatted message as `{"role": "user", "content": "## 当前任务进度（压缩后恢复）\n\n..."}`
- [ ] T016 [US5] Call `_restore_todos(messages)` at the end of `compact_history()` (after successful summary replacement)

**Checkpoint**: Full LLM summarization works, todo progress survives compaction.

---

## Phase 7: Unit Tests (Constitution Principle VII)

**Purpose**: Test all four layers independently. Core module tests are mandatory.

- [ ] T017 [P] Create `tests/test_compact.py` with test fixtures: `sample_messages` (mixed roles, varying sizes), `large_tool_result` (80KB content)

### L3 Tests

- [ ] T018 [P] [US1] Test `tool_result_budget` no-op when under budget in [tests/test_compact.py](tests/test_compact.py)
- [ ] T019 [P] [US1] Test `tool_result_budget` persists and replaces content when over budget in [tests/test_compact.py](tests/test_compact.py)
- [ ] T020 [P] [US1] Test `tool_result_budget` skips when single content ≤30KB even if total over budget in [tests/test_compact.py](tests/test_compact.py)

### L1 Tests

- [ ] T021 [P] [US2] Test `snip_compact` no-op when ≤100 messages in [tests/test_compact.py](tests/test_compact.py)
- [ ] T022 [P] [US2] Test `snip_compact` cuts middle and inserts snipped placeholder in [tests/test_compact.py](tests/test_compact.py)
- [ ] T023 [P] [US2] Test `snip_compact` head boundary protection (tool_calls → tool results pulled in) in [tests/test_compact.py](tests/test_compact.py)
- [ ] T024 [P] [US2] Test `snip_compact` tail boundary protection (lone tool result → assistant pulled in) in [tests/test_compact.py](tests/test_compact.py)
- [ ] T025 [P] [US2] Test `snip_compact` skips when head_end ≥ tail_start (total overlap from protection) in [tests/test_compact.py](tests/test_compact.py)

### L2 Tests

- [ ] T026 [P] [US3] Test `micro_compact` no-op when ≤5 tool_results in [tests/test_compact.py](tests/test_compact.py)
- [ ] T027 [P] [US3] Test `micro_compact` replaces old long results, keeps recent 5 intact in [tests/test_compact.py](tests/test_compact.py)
- [ ] T028 [P] [US3] Test `micro_compact` skips short content (≤120 chars) even when old in [tests/test_compact.py](tests/test_compact.py)

### L4 Tests

- [ ] T029 [P] [US4] Test `compact_history` saves transcript and replaces messages on success in [tests/test_compact.py](tests/test_compact.py)
- [ ] T030 [P] [US4] Test `compact_history` preserves system message in [tests/test_compact.py](tests/test_compact.py)
- [ ] T031 [P] [US4] Test `compact_history` retries then degrades on persistent failure in [tests/test_compact.py](tests/test_compact.py)

### US5 Tests

- [ ] T032 [P] [US5] Test `_restore_todos` appends formatted todo list when CURRENT_TODOS non-empty in [tests/test_compact.py](tests/test_compact.py)
- [ ] T033 [P] [US5] Test `_restore_todos` no-op when CURRENT_TODOS empty in [tests/test_compact.py](tests/test_compact.py)

### Integration Tests

- [ ] T034 Test `compact_pipeline` runs L3→L1→L2→L4 in correct order in [tests/test_compact.py](tests/test_compact.py)
- [ ] T035 Test `compact_pipeline` short conversation is transparent (no messages changed, no files created) in [tests/test_compact.py](tests/test_compact.py)

**Checkpoint**: All 19 tests pass. Full coverage of normal paths, boundaries, and error paths.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup

- [ ] T036 Run quickstart.md validation scenarios end-to-end in [specs/003-context-compact/quickstart.md](specs/003-context-compact/quickstart.md)
- [ ] T037 Verify Agent runs correctly with real LLM (not just unit tests) — a short question produces correct answer without compact interference
- [ ] T038 Review `agent/compact.py` for code clarity: add docstrings, ensure "why" comments on L3-before-L2 ordering, boundary protection logic

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup (T001, T002) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational (T003-T006)
- **US2 (Phase 4)**: Depends on Foundational (T003-T006). Independent from US1.
- **US3 (Phase 5)**: Depends on Foundational (T003-T006). Independent from US1, US2.
- **US4+US5 (Phase 6)**: Depends on Foundational. US5 depends on US4 (T015 after T013). Independent from US1, US2, US3.
- **Tests (Phase 7)**: Depends on all implementation phases (US1-US5)
- **Polish (Phase 8)**: Depends on Tests passing

### User Story Dependencies

- **US1 (L3)**: Independent — can start after Foundational
- **US2 (L1)**: Independent — can start after Foundational
- **US3 (L2)**: Independent — can start after Foundational
- **US4+US5 (L4+todo)**: Independent of US1-US3, but US5 depends on US4

### Within Each Phase

- T003 before T004 before T005 (foundational builds up)
- T013 before T014 before T015 before T016 (US4 builds up, then US5 depends on US4)
- T017 (fixtures) before T018-T035 (tests)

### Parallel Opportunities

- T001 and T002 can run in parallel (Setup)
- T003 and T004 can run in parallel (Foundational helpers)
- US1 (T007-T008), US2 (T009-T010), US3 (T011-T012) can all be implemented in parallel after Foundational
- All test tasks T018-T033 can run in parallel (different test functions, same file but non-overlapping)
- T036 and T038 can run in parallel (Polish)

---

## Parallel Examples

### Foundational Phase

```bash
# Launch in parallel:
Task: "T003 Implement _estimate_size() in agent/compact.py"
Task: "T004 Implement OpenAI-format message helpers in agent/compact.py"
```

### User Stories (after Foundational)

```bash
# All three P1/P2 stories can be implemented in parallel:
Task: "T007 [US1] Implement tool_result_budget() in agent/compact.py"
Task: "T009 [US2] Implement snip_compact() in agent/compact.py"
Task: "T011 [US3] Implement micro_compact() in agent/compact.py"
```

### Tests

```bash
# All unit tests can be written in parallel:
Task: "T018-T020 [US1 tests]"
Task: "T021-T025 [US2 tests]"
Task: "T026-T028 [US3 tests]"
Task: "T029-T033 [US4+US5 tests]"
```

---

## Implementation Strategy

### MVP First (US1 only — L3 Tool Result Budget)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T006)
3. Complete Phase 3: US1 L3 (T007-T008)
4. **STOP and VALIDATE**: Run a bash command producing >30KB output, verify persistence
5. L3 alone provides the most critical defense — a single large tool output won't crash the Agent

### Incremental Delivery

1. Setup + Foundational → skeleton wired in
2. + US1 (L3) → big outputs don't crash → **MVP ready**
3. + US2 (L1) → long conversations don't bloat → test independently
4. + US3 (L2) → old results cleaned → test independently
5. + US4+US5 (L4+todo) → full LLM summary with state recovery → test independently
6. + Tests → all 19 tests pass
7. + Polish → quickstart validated

### Recommended Execution (single developer)

T001 → T002 → T003 → T004 → T005 → T006 → (checkpoint) → T007 → T008 → T009 → T010 → T011 → T012 → T013 → T014 → T015 → T016 → T017 → T018-T033 → T034 → T035 → T036 → T037 → T038

---

## Notes

- US4's `compact_history` is the only function needing `LLMClient` — all others are pure message transforms
- L3 MUST run before L2 in the pipeline (documented in T005 orchestrator)
- All compact functions operate in-place on the messages list — no return value
- System message preservation in L4 is critical: the first message IS the system prompt
- [P] tasks share `agent/compact.py` but target different functions — no merge conflicts expected
