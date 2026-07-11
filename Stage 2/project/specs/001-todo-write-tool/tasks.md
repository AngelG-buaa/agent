# Tasks: TodoWrite Tool

**Input**: Design documents from [specs/001-todo-write-tool/](./)

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

**Tests**: Core module tests are **mandatory** per constitution Principle VII (Agent loop and Tool execution pipeline are core modules).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

Project root: `d:/LLM/Agent/Stage 2/project/`
Existing structure: `agent/`, `tools/`, `tooling/`, `rag/`, `tests/` (to be created)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Minimal initialization needed before feature work begins.

- [x] T001 Create `tests/` directory at project root and add empty `tests/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core todo_write tool implementation — needed by ALL user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T002 [P] Create `TodoWriteTool` class in `tools/todo_write.py` — inherit `Tool` base, implement `run()` (validate todos, update global list, print display) and `get_parameters()` (define `todos` array parameter with `content` + `status` fields)
- [x] T003 [P] Update `agent/prompts.py` — append concise todo workflow guidance to `SYSTEM_PROMPT`: "plan before execute" with pending→in_progress→completed flow
- [x] T004 Register `TodoWriteTool` in `tools/__init__.py` `register_all()` — import and add `executor.register(TodoWriteTool())` alongside existing tools

**Checkpoint**: todo_write tool is available to Agent, prompt includes planning guidance. US1/US2/US3 can now begin.

---

## Phase 3: User Story 1 - Agent 规划步骤 (Priority: P1) 🎯 MVP

**Goal**: Agent uses todo_write to list steps before executing complex multi-step tasks.

**Independent Test**: Give Agent a 3+ step task (e.g., "refactor Python file: add type hints, docstring, main guard"), verify Agent calls todo_write before other tools, and updates statuses through in_progress → completed.

### Tests for User Story 1 ⚠️

> **NOTE**: Core module tests — mandatory per constitution Principle VII. Write tests FIRST, ensure they FAIL before implementation.

- [x] T005 [P] [US1] Write TodoWriteTool unit tests in `tests/test_todo_write.py` — test: valid todos creation (pending/in_progress/completed), invalid status rejection, empty content rejection, empty list handling, consecutive calls replace previous list
- [x] T006 [P] [US1] Write Agent-level integration test in `tests/test_todo_write.py` — mock LLM to return a todo_write tool_call followed by text response, verify the tool is executed and todos are stored

### Implementation for User Story 1

- [x] T007 [US1] Run quickstart validation scenario 3: give Agent a real multi-step task, observe whether todo_write is called before other tools, check status transitions in terminal output

**Checkpoint**: Agent can plan multi-step tasks with todo_write. Tool behavior is tested and validated.

---

## Phase 4: User Story 2 - 任务进度可视化 (Priority: P1)

**Goal**: Users see a clearly formatted task list with status icons after each todo_write call.

**Independent Test**: Call todo_write with 3 tasks of different statuses, verify terminal output contains `[ ]` / `[▸]` / `[✓]` icons and readable task content.

### Tests for User Story 2 ⚠️

> **NOTE**: Same convention — mandatory for core modules.

- [x] T008 [US2] Write display format tests in `tests/test_todo_write.py` — test: output contains icon mapping for each status, output includes task content text, ordering matches input order, update replaces previous display

### Implementation for User Story 2

- [x] T009 [US2] Verify display format matches contract in `contracts/todo-write-schema.md` — run the tool with sample data and check exact output format

**Checkpoint**: Task progress is clearly visible to users. Display format is validated.

---

## Phase 5: User Story 3 - 规划提醒 (Priority: P2)

**Goal**: When Agent goes 3 consecutive rounds without calling todo_write, a reminder is injected to nudge it back to planning.

**Independent Test**: Simulate Agent loop with 3 rounds of non-todo_write calls, verify reminder message is injected on 4th round. Verify todo_write call resets counter.

### Tests for User Story 3 ⚠️

> **NOTE**: Agent loop is a core module — tests mandatory per constitution Principle VII.

- [x] T010 [P] [US3] Write counter logic unit tests in `tests/test_todo_write.py` — test: counter starts at 0, increments after each non-todo_write round (including text-only rounds), resets to 0 on todo_write call
- [x] T011 [P] [US3] Write reminder injection tests in `tests/test_todo_write.py` — test: counter reaches 3 → reminder injected, reminder is `role: "user"` with content `<reminder>Update your todos.</reminder>`, counter resets to 0 after injection, reminder re-triggers after 3 more ignored rounds

### Implementation for User Story 3

- [x] T012 [US3] Add round counter and reminder logic in `agent/agent.py` `Agent.run()` — initialize `rounds_since_todo = 0` before loop, increment at end of each loop iteration if todo_write was NOT called, inject reminder when counter reaches 3, reset counter on todo_write call or reminder injection

**Checkpoint**: Agent is nudged to maintain its todo list. Counter and reminder logic is tested.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Regression validation and final verification.

- [x] T013 Run full unit test suite: `python -m pytest tests/test_todo_write.py -v` — all tests must pass
- [x] T014 [P] Run quickstart regression scenario 5: verify existing tools (get_time, calculator) still work unchanged after todo_write registration
- [x] T015 Run quickstart validation scenario 3 (end-to-end): real Agent task with todo_write, verify complete flow
- [x] T016 [P] Review code against Constitution Check in plan.md — verify no principle violations before merge

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — starts immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational — P1, MVP
- **User Story 2 (Phase 4)**: Depends on Foundational — P1, parallelizable with US1
- **User Story 3 (Phase 5)**: Depends on Foundational — P2, Agent loop change is independent of US1/US2
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependencies on other stories
- **User Story 2 (P1)**: Can start after Foundational — uses same tool as US1, independently testable
- **User Story 3 (P2)**: Can start after Foundational — Agent loop change is in a different file

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Tests before validation
- Story complete before declaring checkpoint

### Parallel Opportunities

- T002 (todo_write.py) and T003 (prompts.py) can run in parallel
- T005 (US1 tests) and T008 (US2 tests) and T010 (US3 tests) can all run in parallel
- T014 (regression) and T016 (constitution review) can run in parallel

---

## Parallel Example: Phase 2 Foundational

```bash
# Launch T002 and T003 together (different files):
Task: "Create TodoWriteTool class in tools/todo_write.py"
Task: "Update SYSTEM_PROMPT in agent/prompts.py"
```

## Parallel Example: All User Story Tests

```bash
# Launch all test writing tasks together (different test functions):
Task: "Write TodoWriteTool unit tests in tests/test_todo_write.py [US1]"
Task: "Write display format tests in tests/test_todo_write.py [US2]"
Task: "Write counter logic unit tests in tests/test_todo_write.py [US3]"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T004)
3. Complete Phase 3: User Story 1 (T005–T007)
4. **STOP and VALIDATE**: Agent can plan tasks with todo_write ✅
5. Demo if ready — this is the core value proposition

### Incremental Delivery

1. Setup + Foundational → todo_write tool exists
2. Add User Story 1 → Agent plans tasks → Test → **MVP!**
3. Add User Story 2 → Visual progress display → Test → UX complete
4. Add User Story 3 → Nag reminder → Test → Robustness complete
5. Polish → Regression tests + Constitution review → **Ready to merge**

### Recommended Order (Single Developer)

T001 → T002 + T003 (parallel) → T004 → T005 → run tests (FAIL) → T007 (validate with real Agent) → T006 (pass after T004) → T008 → T009 → T010 + T011 → T012 → T013 → T014 + T016 → T015

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing corresponding code
- Commit after each phase or logical task group
- Stop at any checkpoint to validate story independently
- Maximum file size per new file: target <150 lines for todo_write.py
- **Tests are mandatory for all user stories** — TodoWrite tool and Agent loop are both core modules per constitution Principle VII
