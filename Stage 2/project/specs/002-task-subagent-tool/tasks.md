# Tasks: Task Tool & Sub-Agent

**Input**: Design documents from `/specs/002-task-subagent-tool/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, quickstart.md ✅

**Tests**: Per constitution Principle VII, `SubAgent` (Agent loop variant) and `TaskTool` (Tool execution) are core modules — tests are **mandatory**.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story (US1, US2, US3)
- Exact file paths in every description

## Path Conventions

All paths relative to `Stage 2/project/`: `agent/`, `tools/`, `tooling/`, `tests/`

---

## Phase 1: Setup

**Purpose**: Confirm environment is ready.

- [x] T001 Verify Python 3.12+ and existing test suite passes (`pytest tests/`)

---

## Phase 2: Foundational — Agent Extensions + SubAgent

**Purpose**: Add `tool_filter`、`print_handler` to `Agent`, create `agent/utils.py` with print callbacks, and implement `SubAgent(Agent)` subclass. These are blocking prerequisites for all user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T002 [P] Add `SUB_SYSTEM_PROMPT` constant to `agent/prompts.py`. Three sections: (1) "你是一个子代理（Sub-Agent），由主 Agent 委派执行具体子任务", (2) "直接使用工具完成任务，不要尝试再次委派", (3) "返回结论性结果". Do NOT include main Agent's general behavior guidelines or TodoWrite workflow.

- [x] T003 [P] Add `tool_filter: set[str] | None = None` parameter to `Agent.__init__()` in `agent/agent.py`. In `run()`, filter schemas BEFORE the loop: `if self.tool_filter: schemas = [s for s in schemas if s["function"]["name"] not in self.tool_filter]`. Default `None` preserves existing behavior.

- [x] T004 [P] Create `agent/utils.py` with three functions: (a) `default_print_handler(name, args)` — prints `🔧 调用工具: {name}({args})`, (b) `sub_print_handler(name, args)` — prints `[sub] {name}({summary})` where summary comes from `_extract_key_param`, (c) `_extract_key_param(name, args)` — extracts key param per tool type (command[:60] for bash, file_path for file tools, pattern for glob, query[:60] for search, url[:60] for fetch, empty string for others). Then add `print_handler: Callable | None = None` parameter to `Agent.__init__()` in `agent/agent.py`, with `from agent.utils import default_print_handler` and `self.print_handler = print_handler or default_print_handler`. In `_execute_tool_calls()`, replace `print(f"  🔧 调用工具: {name}({args})")` with `self.print_handler(name, args)`.

- [x] T005 Implement `SubAgent(Agent)` subclass in `agent/agent.py` (below the `Agent` class definition):
  - `__init__(self, llm, executor)`: calls `super().__init__(llm=llm, executor=executor, system_prompt=SUB_SYSTEM_PROMPT, max_steps=30, tool_filter={"task", "todo_write"}, print_handler=sub_print_handler)`. Initializes `self._round = 0`.
  - `_execute_tool_calls(self, tool_calls, messages)`: increments `self._round += 1`. If `self._round == 30`, appends `{"role": "user", "content": "你已达到最大轮数限制，请基于已有信息给出当前最佳结论。"}` to messages before calling `super()._execute_tool_calls(tool_calls, messages)`. **注意时机**：提醒在第 30 轮工具调用前注入到 messages 中，SubAgent 的 LLM 会在同一轮内看到它并应返回文本回复。若 LLM 仍返回 tool_calls，`max_steps=30` 的 `range(30)` 循环在第 30 次迭代后自然结束，`Agent.run()` 返回最后一条消息文本作为兜底。这意味着提醒是最佳路径，硬截断是兜底。
  - Imports: `from agent.prompts import SUB_SYSTEM_PROMPT`, `from agent.utils import sub_print_handler`.

- [x] T006 Write tests for Phase 2 in `tests/test_task.py`:
  - `tool_filter=None` preserves all schemas
  - `tool_filter={"task", "todo_write"}` removes exactly those two
  - `print_handler` default uses `default_print_handler` format
  - Custom `print_handler` is called instead of default
  - `SubAgent(llm, executor)` has `max_steps=30`, `tool_filter={"task", "todo_write"}`, `system_prompt=SUB_SYSTEM_PROMPT`
  - `SubAgent._round` increments on each `_execute_tool_calls()` call
  - `SubAgent` injects reminder at round 30
  - `SubAgent` shares executor with main Agent (same instance)

**Checkpoint**: Foundation ready — Agent can accept filters and custom print handlers. SubAgent class exists with round tracking and reminder injection. All tests pass.

---

## Phase 3: User Story 1 + 2 — Core Sub-Agent Delegation & Safety (Priority: P1) 🎯 MVP

**Goal**: Agent can call `task` tool to spawn a SubAgent that executes independently with safety constraints (no recursion, max 30 rounds, permission checks) and returns only the final text conclusion.

**Independent Test**: Give Agent "用 task 工具检查 tools/ 目录下有多少 .py 文件" — verify (a) SubAgent launches, (b) returns file count, (c) main Agent message list does NOT contain SubAgent's intermediate reads.

### Implementation for US1 + US2

- [x] T007 [US1] Create `TaskTool` class in `tools/task.py`:
  - Inherits `Tool`. `name="task"`, `description` explains delegation scenarios (complex multi-step subtasks, clean context benefit)
  - Parameter: `description` (string, required)
  - `__init__` accepts optional `llm` and `executor` params (stored as `self._llm`, `self._executor`), default `None`. Provides `set_context(llm, executor)` setter for post-construction wiring
  - `run(parameters)`: validates description non-empty → returns `{"error": "description is required"}` if empty. Otherwise calls `spawn_subagent(description, self._llm, self._executor)` and returns `{"result": result}`. Catches exceptions → `{"error": str(e)}`

- [x] T008 [US1] Implement `spawn_subagent(description: str, llm, executor) -> str` in `tools/task.py`:
  - Print `[Subagent spawned] {description[:100]}`
  - Create `SubAgent(llm=llm, executor=executor)`
  - Call `sub.run(description)` and store result
  - Print `[Subagent done]`
  - Return result text
  - Imports: `from agent.agent import SubAgent`

- [x] T009 [US1] Register and wire `TaskTool` in `tools/__init__.py` `register_all()` and `main.py`:
  - In `tools/__init__.py`: `executor.register(TaskTool())` alongside existing registrations
  - In `main.py`: after `register_all(executor, ...)`, call `executor._registry.get_tool("task").set_context(llm, executor)` to wire the LLM client and executor references

- [x] T010 [US1] [US2] Write core tests in `tests/test_task.py`:
  - `TaskTool.run({"description": ""})` returns `{"error": "description is required"}`
  - `TaskTool.run({"description": "list files"})` returns `{"result": ...}` (non-empty)
  - `spawn_subagent()` returns string (not dict, not None)
  - SubAgent tool set excludes "task" and "todo_write" (verify via `tool_filter`)
  - SubAgent has `max_steps=30`
  - SubAgent shares main Agent's executor (same `id(executor)`)
  - SubAgent fresh messages list starts with system + user description only

**Checkpoint**: MVP complete — Agent can delegate to SubAgent with full safety constraints. Run quickstart Scenario 1 to validate.

---

## Phase 4: User Story 3 — Observability (Priority: P2)

**Goal**: User can distinguish main Agent and SubAgent actions in terminal output.

**Independent Test**: Run a task that triggers SubAgent — verify terminal shows `[Subagent spawned]`, `[sub]`-prefixed tool calls, and `[Subagent done]`.

### Implementation for US3

- [x] T011 [US3] Verify and refine output markers:
  - `[Subagent spawned]` prints before `SubAgent()` creation in `spawn_subagent()` (already in T008)
  - `[Subagent done]` prints after `sub.run()` returns (already in T008)
  - `[sub]` prefix via `sub_print_handler` in `agent/utils.py` (already in T004)
  - `_extract_key_param` produces meaningful summaries for all 10 available tools — verify bash, read_file, write_file, edit_file, glob, read_chunk, web_search, web_fetch, search_knowledge, calculator, get_time

- [x] T012 [P] [US3] Write observability test in `tests/test_task.py`:
  - Mock `print` or capture stdout: verify `sub_print_handler` output matches `[sub] name(key_param)` format
  - Verify `_extract_key_param` coverage for all tool types
  - Verify `default_print_handler` output format unchanged

**Checkpoint**: User can clearly see SubAgent lifecycle and distinguish it from main Agent.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Integration validation and edge case hardening.

- [x] T013 [P] Write edge case tests in `tests/test_task.py`:
  - TaskTool with missing "description" key → graceful error
  - SubAgent hitting max rounds returns partial result (mock LLM that always returns tool_calls)
  - LLM API error during SubAgent execution → error returned as `{"error": ...}` not exception crash
  - All tool calls denied by permission → SubAgent returns explanation (non-empty)

- [x] T014 Run quickstart.md validation scenarios 1–5 and confirm all pass. Fix any issues.

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
  └─► Phase 2 (Foundational: Agent + SubAgent + utils)
        ├─► Phase 3 (US1+US2: Core Task Tool) 🎯 MVP
        │     └─► Phase 4 (US3: Observability check)
        │           └─► Phase 5 (Polish)
```

### User Story Dependencies

| Story | Depends On | Independent Test |
|-------|-----------|------------------|
| US1+US2 (P1) | Phase 2 | Delegate a file-counting subtask via task tool |
| US3 (P2) | US1+US2 (needs real SubAgent output to observe) | Visual inspection of terminal markers |

### Within Each Phase

- Phase 2: T002 [P], T003 [P], T004 [P] — three different files/concerns, can run in parallel. T005 depends on T002+T003+T004 (uses all three). T006 validates entire Phase 2.
- Phase 3: T007 → T008 (spawn_subagent depends on TaskTool design), T009 after T008. T010 validates Phase 3.
- Phase 4: T011 after Phase 3 (needs running code to inspect). T012 [P].
- Phase 5: T013 [P], then T014.

### Parallel Opportunities

```bash
# Phase 2: Launch three independent Agent-extension tasks together
Task: "T002 - SUB_SYSTEM_PROMPT in agent/prompts.py"
Task: "T003 - tool_filter on Agent in agent/agent.py"
Task: "T004 - agent/utils.py + print_handler on Agent"

# After Phase 2 completes: T006 can run while T007 starts
# Phase 4: T012 can run alongside T011
# Phase 5: T013 can run while someone else does T014
```

---

## Parallel Example: Phase 2 (Foundational)

```bash
# All three modify different files/concerns — run in parallel:
Task: "T002 - SUB_SYSTEM_PROMPT in agent/prompts.py (~10 lines)"
Task: "T003 - tool_filter in agent/agent.py (~6 lines)"
Task: "T004 - agent/utils.py + print_handler in agent/agent.py (~35 lines)"

# Then sequential:
Task: "T005 - SubAgent class in agent/agent.py (depends on T002+T003+T004)"
Task: "T006 - Phase 2 tests (validates everything above)"
```

---

## Implementation Strategy

### MVP First (Phase 1 → 2 → 3)

1. T001: Verify dev environment
2. T002–T004 (parallel): Agent extensions + utils
3. T005: SubAgent class
4. T006: Validate with tests
5. T007: TaskTool class
6. T008: spawn_subagent function
7. T009: Wire up registration
8. T010: Core tests
9. **STOP and VALIDATE**: Run `quickstart.md` Scenario 1 — basic delegation works
10. **MVP READY**

### Full Delivery

11. T011–T012: Observability
12. T013: Edge case tests
13. T014: Full quickstart validation

### Files Changed Summary

| File | Tasks | Type | Lines |
|------|-------|------|-------|
| `agent/prompts.py` | T002 | +constant | ~10 |
| `agent/agent.py` | T003, T004, T005 | +params +SubAgent class | ~35 |
| `agent/utils.py` | T004 | **new file** | ~30 |
| `tools/task.py` | T007, T008 | **new file** | ~50 |
| `tools/__init__.py` | T009 | +register | +1 |
| `main.py` | T009 | +wiring | +2 |
| `tests/test_task.py` | T006, T010, T012, T013 | **new file** | ~100 |

**Agent.run() loop body: 0 lines.** SubAgent._execute_tool_calls() overrides parent — 0 lines in the loop itself.

## Notes

- **No hook register/unregister**: `SubAgent._execute_tool_calls()` handles round tracking via instance variable. Zero hook dependency.
- **Dependency direction**: `tools/task.py` → `agent/agent.py` → `agent/utils.py` → `agent/prompts.py`. Clean, no cycles.
- **TaskTool wiring**: `TaskTool` needs `llm` + `executor` references to call `spawn_subagent()`. After `register_all()`, call `task_tool.set_context(llm, executor)` in `main.py`.
- **Permission session**: `SubAgent` shares main Agent's `executor` → same `PermissionEngine` → same session rules. Zero extra code.
- **SubAgent recursion prevention**: `tool_filter={"task", ...}` → LLM never sees `task` in schemas → cannot call it.
- Commit after each phase checkpoint.
