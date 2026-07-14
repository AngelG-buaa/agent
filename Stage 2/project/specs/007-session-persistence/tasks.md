# Tasks: Session 持久化（结构重构版）

**Input**: Design documents from [specs/007-session-persistence/](./)

**Prerequisites**: [plan.md](./plan.md) ✅, [spec.md](./spec.md) ✅, [research.md](./research.md) ✅, [data-model.md](./data-model.md) ✅, [contracts/](./contracts/) ✅

**Tests**: Core module tests are mandatory per Constitution VII. The `Agent.run → on_message → Controller → Repository` chain is a core path and requires integration tests.

**Organization**: Tasks are organized by correctness priority first, then user story, then refactoring. P0 fixes (Phase 2) block all user stories.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Verify existing infrastructure is ready; no new files needed.

- [x] T001 Verify all existing tests pass before any changes: `python -m pytest tests/ -q -k "not test_compact"`
- [x] T002 Verify `.myagent/` is in `.gitignore` to prevent session DBs from being committed

---

## Phase 2: Foundational — P0 正确性修复

**Purpose**: Fix the three correctness defects that affect all user stories. **⚠️ CRITICAL**: No user story works correctly until these are fixed.

**Bug summary** ([plan.md §1](./plan.md#1-根因分析与优先级)):
- `_emit_message` causes memory double-append (SQLite correct, working context duplicate)
- final assistant never persisted (neither DB nor memory)
- system prompt never persisted (empty string in DB)

### S1: Fix `_emit_message` sink semantics

- [x] T003 [P] Fix `_emit_message()` in `agent/agent.py` L53-64: change from `if on_message: on_message(msg); messages.append(msg)` to `if on_message: on_message(msg) else: messages.append(msg)` — `on_message` is a complete sink, Agent does not append again after calling it

### S2: Persist final assistant

- [x] T004 In `Agent.run()` in `agent/agent.py` L118-126: before `return msg.content`, add `final_msg = _normalize_message(msg)` + `_emit_message(final_msg, messages, on_message)`. Move `trigger_hooks("PreAgentStop", messages)` to AFTER `_emit_message` so final assistant is in working context when hook fires. T030-T032 later move this call to public `normalize_message()`

### S3: Write real system prompt at creation time

- [x] T005 Add `system_message: dict` parameter to `SessionController.__init__` in `agent/conversation.py` L40-52; store as `self._system_message`
- [x] T006 Update `SessionController.start_new()` in `agent/conversation.py` L58-80: pass `self._system_message` to `create_session()` instead of `{"role":"system","content":""}`; use `self._system_message` for `ActiveSession.messages` initial value (copy with `dict(self._system_message)`)
- [x] T007 Remove role=="system" special-case branch from `SessionController.append_message()` in `agent/conversation.py` L126-134; unify to single path: `self._mgr.append_message(self.active.id, message)` + `self.active.messages.append(message)` for all roles
- [x] T008 Remove first-turn system prompt injection from `Conversation._run_turn()` in `agent/conversation.py` L290-298 (the `if not self.messages or (len(self.messages) == 1 and self.messages[0]["role"] == "system" and self.messages[0]["content"] == "")` block)
- [x] T009 Update `Conversation.__init__` in `agent/conversation.py` L183-205: accept `system_message: dict | None = None`; pass to `SessionController`; raise `ValueError` if persistent mode (`session_manager` + `permission_engine` provided) without `system_message`; raise `ValueError` if only one of `session_manager`/`permission_engine` provided (no silent degradation to transient)
- [x] T010 Update `main.py`: pass `system_message={"role":"system","content":SYSTEM_PROMPT}` to `Conversation()` constructor

**Checkpoint**: After T003-T010, new session has correct system prompt in DB, messages never duplicate in memory, and final assistant is persisted. Verify with:
```bash
python -m pytest tests/ -q -k "not test_compact"
# manual: sqlite3 .myagent/sessions/*.db "SELECT role, content FROM messages ORDER BY seq;"
```

---

## Phase 3: User Story 1 — 会话自动持久化与退出恢复 (Priority: P1) 🎯 MVP

**Goal**: Users start a session, all messages auto-persist, exit, and resume with complete context (messages, Todo, permissions).

**Independent Test**: `python main.py` → one conversation round → exit → `python main.py --resume` → select session → verify context continuity.

### Tests for User Story 1

- [x] T011 [P] [US1] Add `on_message=None` parameter to `_FakeAgent.run()` signature in `tests/test_conversation.py` L17 — `def run(self, messages: list[dict], on_message=None) -> str:` — to match real `Agent.run()` interface
- [x] T012 [P] [US1] New test `test_full_roundtrip_via_agent_run` in `tests/test_session_persistence.py` — mock LLM returns final answer only (no tool calls). Verify: DB has 3 messages (system→user→assistant), system content is "You are helpful.", assistant content is mock response, no duplicate messages (memory count == DB count)
- [x] T013 [P] [US1] New test `test_tool_roundtrip_via_agent_run` in `tests/test_session_persistence.py` — mock LLM returns tool_calls then final answer. Verify: full chain system→user→assistant(tool_calls)→tool→assistant(final) all persisted with correct seq order, no duplicates
- [x] T014 [US1] New test `test_subagent_messages_not_persisted` in `tests/test_session_persistence.py` — spawn SubAgent via Task tool, verify SubAgent's internal messages (its system prompt, intermediate turns) do not appear in main session DB

### Implementation for User Story 1

> Core implementation (message persistence, system prompt, final assistant) is in Phase 2 T003-T010. This phase adds integration tests to verify the full chain.

- [x] T015 [US1] Ensure `Agent.run()` passes `on_message` for all message types: assistant with tool_calls (already done in T003), tool results (already via `_execute_tool_calls`), and final assistant (done in T004). Verify by running T012 and T013

**Checkpoint**: `TestRealPersistenceChain` passes. A user can start a conversation, have all messages persisted correctly, exit, and resume with full context.

---

## Phase 4: User Story 2 — Session 列表管理与操作 (Priority: P2)

**Goal**: Users browse, delete, and rename historical sessions from `--resume` and `/resume`.

**Independent Test**: Create 3 sessions → `--resume` → verify list → delete one → verify refresh → rename one → verify title.

### Tests for User Story 2

- [x] T016 [P] [US2] Verify existing `test_empty_session_removed` in `tests/test_session_persistence.py` still passes with new architecture
- [x] T017 [P] [US2] Verify existing `test_nonempty_session_kept` in `tests/test_session_persistence.py` still passes
- [x] T018 [P] [US2] Verify existing `test_auto_title_from_first_user_message` in `tests/test_session_persistence.py` still passes

### Implementation for User Story 2

- [x] T019 [US2] Verify `SessionController.list_sessions()` delegates to `SessionManager.list_sessions()` — regression check after T005-T007
- [x] T020 [US2] Verify `SessionController.rename()` syncs `active.title` when renaming current active session — implement if missing: `if self.active is not None and session_id == self.active.id: self.active.title = title`
- [x] T021 [US2] Verify `SessionController.delete()` blocks deletion of current active session with `ActiveSessionDeletionError` — regression check

**Checkpoint**: Session list, delete, and rename work via `--resume` and `/resume`.

---

## Phase 5: User Story 3 — REPL 内会话切换 (Priority: P3)

**Goal**: Users type `/resume` during conversation, browse sessions, switch without losing current session state.

**Independent Test**: Session A → talk → `/resume` → switch to B → verify B context → `/resume` → switch back to A → verify A preserved.

### Tests for User Story 3

- [x] T022 [P] [US3] Verify existing `test_cancel_resume_preserves_active` in `tests/test_session_persistence.py` still passes
- [x] T023 [P] [US3] Verify existing `test_load_failure_preserves_active` in `tests/test_session_persistence.py` still passes
- [x] T024 [P] [US3] Verify existing `test_delete_other_while_active_preserves_active` in `tests/test_session_persistence.py` still passes

### Implementation for User Story 3

- [x] T025 [US3] Verify `/resume` REPL flow end-to-end after Phase 2-3: list shows correct sessions, switch loads correct messages/Todo/permissions, cancel preserves active, switching to current active is no-op

**Checkpoint**: REPL session switching works. Cancel and failure cases preserve active session.

---

## Phase 6: User Story 4 — 权限跨 Session 隔离 (Priority: P3)

**Goal**: Permission grants from session A do not leak into session B. Switching sessions atomically replaces all permission rules.

**Independent Test**: Allow bash in session A → exit → resume A → bash still allowed → switch to B → bash requires confirmation.

### Tests for User Story 4

- [x] T026 [P] [US4] Verify existing `test_grants_replaced_on_switch` in `tests/test_session_persistence.py` still passes
- [x] T027 [P] [US4] Verify existing `test_new_session_replaces_rules` in `tests/test_session_persistence.py` still passes

### Implementation for User Story 4

- [x] T028 [US4] Verify `SessionController._on_grant` persists grants to correct active session after T005 changes
- [x] T029 [US4] Verify `SessionController.resume()` calls `engine.replace_session_rules(snap.permissions)` with correct grants — regression check

**Checkpoint**: Permission isolation works across sessions.

---

## Phase 7: Polish — 结构重构

**Purpose**: Improve code organization without changing behavior. All user stories must be passing before this phase.

### S5: Merge `normalize_message()` into `agent/utils.py`

- [x] T030 [P] Add `normalize_message(msg) -> dict` to `agent/utils.py` — single entry point for SDK object → dict conversion. Merges `Agent._normalize_message()` and `filter_assistant_message()`. Output only: role, content (if not None), tool_calls (if not None/empty), tool_call_id (if not None)
- [x] T031 [P] Delete `filter_assistant_message()` from `agent/utils.py` L37-60
- [x] T032 Delete `_normalize_message()` function from `agent/agent.py` L19-50; update all call sites to use `from agent.utils import normalize_message`

### S6: `register_hook()` returns disposer

- [x] T033 [P] Update `register_hook()` in `hooks.py` L33-37: return an idempotent `dispose()` closure that removes the callback from `HOOKS[event]`
- [x] T034 Update `TodoReminderHandle.__init__` in `tools/todo_write.py` L139-143: accept `(pre_disposer, post_disposer)` instead of `(pre_callback, post_callback)`; rename `_pre_callback`/`_post_callback` to `_pre_disposer`/`_post_disposer`
- [x] T035 Update `TodoReminderHandle.dispose()` in `tools/todo_write.py` L154-163: call `self._pre_disposer()` + `self._post_disposer()` instead of directly accessing `HOOKS`
- [x] T036 Update `register_todo_hooks()` in `tools/todo_write.py` L166-202: capture `register_hook()` return values and pass as disposers to `TodoReminderHandle` constructor

### S7: Extract `agent/session_controller.py`

- [x] T037 [P] Create `agent/session_controller.py` with: `ActiveSession` dataclass, `ActiveSessionDeletionError` exception, and `SessionController` class (full implementation from plan.md §S7). Controller owns: active lifecycle, message sink, grant listener, Todo persistence hook (with disposer), reminder handle. `close()` uses try/finally for 6 cleanup steps
- [x] T038 [P] Remove `ActiveSession` dataclass (L26-31) and `ActiveSessionDeletionError` (L72-73) from `agent/session_manager.py`
- [x] T039 Remove `SessionController` class definition (L26-169), `_register_todo_persistence_hook()` method (L393-408), and `resume_session()` test helper (L414-421) from `agent/conversation.py`
- [x] T040 Update `agent/conversation.py` imports: add `from agent.session_controller import SessionController, ActiveSession, ActiveSessionDeletionError`; remove direct `SessionManager` import

### S8: Rewrite Conversation menus + clean main.py + clean session_manager.py

- [ ] T041 Rewrite `Conversation.start()` in `agent/conversation.py` to `start(resume: bool = False)` — unified entry. If `resume=True`, show `_startup_menu(sessions)`; otherwise create new session and enter REPL
- [ ] T042 Add `_startup_menu(sessions)` to `agent/conversation.py` — loop-based (not recursive) session list for `--resume`. Cancel or empty-list → create new session. Resume failure → re-show list (not create new)
- [ ] T043 Add `_repl_resume_menu(sessions)` to `agent/conversation.py` — loop-based session list for `/resume` in REPL. Cancel/failure preserves active. Switching to current active is no-op
- [ ] T044 Add `_enter_repl()` to `agent/conversation.py` — wraps `_repl_loop()` with `try/finally: controller.close()`
- [ ] T045 Remove `_handle_resume_command()` from `agent/conversation.py` L328-370 (replaced by `_repl_resume_menu`)
- [ ] T046 Remove `resume_session()` test helper from `agent/conversation.py` L414-421
- [ ] T047 [P] Remove `_list_and_act()` function from `main.py` L25-71; remove `from agent import ui as session_ui` import; change launch to `conv.start(resume=args.resume)`
- [x] T048 [P] Delete `_init_schema()` dead code from `agent/session_manager.py` L163-174
- [x] T049 [P] Replace `print(..., file=sys.stderr)` in `SessionManager.list_sessions()` in `agent/session_manager.py` L327-330 with `logger.warning("跳过损坏的 session 文件: %s", fname)`. Add `import logging; logger = logging.getLogger(__name__)` at module top
- [x] T050 [P] Add explicit `list[PermissionGrant]` type annotation to `SessionSnapshot.permissions` in `agent/session_manager.py` L51 using `TYPE_CHECKING` guard

### Final validation

- [x] T051 Run full test suite: `python -m pytest tests/ -q` — all tests pass except pre-existing `test_compact.py` failure
- [ ] T052 Run [quickstart.md](./quickstart.md) validation scenarios 1-7 manually
- [ ] T053 Verify all 14 completion criteria from [plan.md §6](./plan.md#6-完成标准)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — run immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — **BLOCKS all user stories**
- **User Stories (Phase 3-6)**: All depend on Phase 2 completion
  - US1 (Phase 3) must complete first — its integration tests validate the Phase 2 fixes
  - US2 (Phase 4), US3 (Phase 5), US4 (Phase 6) can proceed in any order after US1
- **Polish (Phase 7)**: Depends on all user stories being complete

### Within Phase 2 (Foundational)

```
T003 (_emit_message) ──┐
                         ├──→ Phase 3 (US1 tests depend on all three fixes)
T004 (final assistant) ─┤
                         │
T005 → T006 → T007 → T008 → T009 → T010 (system prompt chain)
```

T003 and T004 are independent. T005-T010 are sequential (all modify related code in conversation.py).

### Within Phase 7 (Polish)

```
S5 normalize_message: T030 → T031∥T032
S6 disposer:          T033 → T034 → T035 → T036
S7 session_controller: T037∥T038 → T039 → T040
S8 menus + cleanup:   T041 → T042 → T043 → T044 → T045 → T046 (conversation, sequential)
                       ∥ T047 (main.py, independent)
                       ∥ T048 → T049 → T050 (session_manager, independent of conversation)
```

### Parallel Opportunities

- **Phase 2**: T003 ∥ T004 ∥ T005 (T003/T004 in agent.py, T005 in conversation.py — different concerns)
- **Phase 3**: T011 ∥ T012 ∥ T013 (different test methods, same file but non-overlapping)
- **Phase 4**: T016 ∥ T017 ∥ T018 (independent verifications)
- **Phase 5**: T022 ∥ T023 ∥ T024
- **Phase 6**: T026 ∥ T027
- **Phase 7**: S5 (T030-T032) ∥ S6 (T033-T036) ∥ S7 (T037-T040) ∥ S8-main.py (T047) ∥ S8-session_manager (T048-T050) — all work on different files

---

## Implementation Strategy

### MVP First (Phase 1 + 2 + 3)

1. T001-T002: Verify baseline
2. T003-T010: Fix all three P0/P1 correctness defects
3. T011-T015: Add integration tests proving the fixes work
4. **STOP and VALIDATE**: `TestRealPersistenceChain` passes, quickstart scenarios 1-3 work

### Incremental Delivery

1. Phase 1+2: Foundation solid (correctness fixed)
2. Phase 3: US1 verified with real integration tests → **MVP!**
3. Phase 4-6: US2/US3/US4 regression verified
4. Phase 7: Structural refactoring without behavior change
5. T051-T053: Final validation

---

## Notes

- T003 and T004 both modify `agent/agent.py` L53-126 — if running sequentially, do T003 first, then T004. They touch adjacent but non-overlapping code blocks
- Phase 2 changes (T005-T009) work in the current location (`agent/conversation.py`). In Phase 7 (T037-T040), SessionController moves to its own file. Accept that code moves twice: once to fix, once to reorganize
- `_FakeAgent.run()` adding `on_message=None` (T011) is backward-compatible — existing callers passing only `messages` continue to work
- After T003 (sink fix), SubAgent behavior is unchanged: SubAgent never passes `on_message`, so it takes `else: messages.append(msg)` — correct
- After T007 (remove system special-case), `append_message` has no role-specific branching — all messages go through the same persist-then-append path
- The old tasks.md (58 tasks, 7 phases) is superseded by this version (53 tasks, 7 phases). New task count is lower because existing infrastructure (SessionManager, UI) is already built and only needs correctness fixes, not ground-up implementation
