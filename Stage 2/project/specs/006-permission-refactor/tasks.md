# Tasks: Permission 系统实例级重构

**Input**: Design documents from `/specs/006-permission-refactor/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Core module (PermissionEngine) — tests **mandatory** per constitution Principle VII.

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- All paths relative to `Stage 2/project/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 新建文件，无依赖。完成后可并行进入后续 Phase。

- [x] T001 Create permission exception types in `tooling/permission/exceptions.py` — define `PermissionArchitectureError`, `NonPersistablePermission`, `InvalidPermissionGrant`, `InvalidPermissionRule` per plan.md Step 1

**Checkpoint**: `python -c "from tooling.permission.exceptions import NonPersistablePermission; print('OK')"`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Engine 内部重构——索引、数据类、eval 管线。所有 User Story 的前置。

**⚠️ CRITICAL**: 所有 user story 都依赖此 Phase 完成。

- [x] T002 Add `PermissionGrant` dataclass to `tooling/permission/engine.py` — `@dataclass(frozen=True)` with `tool_name: str` and `rule_content: str` per plan.md Step 2.2
- [x] T003 Rename `EvalResult.rule` → `EvalResult.matched_rule` in `tooling/permission/engine.py` and update all constructor calls per plan.md Step 2.3 + 3.5
- [x] T004 Build `_policy_rules_by_key` and `_policy_rules_by_id` indexes in `PermissionEngine.__init__()` with duplicate-key detection per plan.md Step 2.4; delete `_session_counter`
- [x] T005 Change `_session_rules` key type from `str` to `tuple[str, str]` in `PermissionEngine.__init__()` per plan.md Step 2.4
- [x] T006 Rewrite `_collect_all_rules()` with explicit override logic (DENY kept, SESSION ALLOW overrides POLICY ASK by key) per plan.md Step 2.5
- [x] T007 Add `_build_session_rule(grant: PermissionGrant) -> PermissionRule` private method in `tooling/permission/engine.py` per plan.md Step 2.6
- [x] T008 Update `tooling/permission/__init__.py` exports — add `PermissionGrant`, exception types; remove `create_permission_hook` and its imports per plan.md Step 6.1

**Checkpoint**: `python -c "from tooling.permission import PermissionEngine, PermissionGrant, NonPersistablePermission; e = PermissionEngine(default_behavior='deny'); print(type(e._policy_rules_by_key))"`  → `<class 'dict'>`

---

## Phase 3: User Story 3 — Grant/Listener 持久化通知 (Priority: P1)

**Goal**: `allow_for_session()` 创建 grant 时通知 listener；`replace_session_rules()` 恢复时不通知；fallback ASK 无法持久化。这是 engine 公开 API 的完成阶段——后续 US1+US2 (executor) 依赖此 Phase 提供的 `allow_for_session(result)` 新签名。

**Independent Test**: 安装 listener → `allow_for_session()` 成功 → listener 调用一次且参数正确；`replace_session_rules()` 不触发 listener；fallback ASK 抛 `NonPersistablePermission`。

**⚠️ CRITICAL**: US1+US2 (executor) 的 `_authorize_tool_call` 调用 `engine.allow_for_session(result)` 新签名，依赖此 Phase 的 T016 完成。

### Implementation

- [x] T009 [US3] Add `set_grant_listener(listener)` method to `PermissionEngine` in `tooling/permission/engine.py` per plan.md Step 3.2
- [x] T010 [US3] Rewrite `allow_for_session(result: EvalResult) -> PermissionGrant` in `tooling/permission/engine.py` — validate ASK + matched_rule non-None + rule ownership → create grant → listener persist → install rule per plan.md Step 3.3
- [x] T011 [US3] Delete old `allow_for_session(tool_name, rule_content, message)`, `revoke_session_rule()`, `clear_session_rules()` from `tooling/permission/engine.py` per plan.md Step 3.1

**Checkpoint**: `python -c "from tooling.permission.engine import PermissionEngine, EvalResult; from tooling.permission.policy import PermissionRule, RuleBehavior; r=PermissionRule('bash', RuleBehavior.ASK, 'rm *', '确认', lambda t,p: True, rule_id='test-1'); e=PermissionEngine([r]); grants=[]; e.set_grant_listener(lambda g: grants.append(g)); g=e.allow_for_session(e.evaluate('bash',{})); assert len(grants)==1; print('OK')"`

---

## Phase 4: User Story 1 & 2 — 实例隔离 + 移除全局权限 Hook (Priority: P1) 🎯 MVP

**Goal**: ToolExecutor 每个实例绑定自己的 PermissionEngine + Approver；权限检查在 executor 内部完成，全局 Hook 仅承载非权限扩展。两个 executor 实例互不干扰。

**Independent Test**: 创建 executor A (deny engine) + executor B (allow engine)，B 执行工具成功不受 A 影响。

**依赖**: Phase 3 (`allow_for_session` 新签名) 必须先完成。

### Implementation

- [x] T012 [US1] Refactor `ToolExecutor.__init__()` in `tooling/executor.py` — accept `permission_engine: PermissionEngine` and `approver: Approver`, store as private fields per plan.md Step 5.2
- [x] T013 [US1] Add `_authorize_tool_call(tool_name, params) -> dict | None` private method in `tooling/executor.py` — evaluate → DENY reject → ALLOW pass → ASK call approver → "session" call `engine.allow_for_session()` with `NonPersistablePermission` catch (degrade to single allow) per plan.md Step 5.4
- [x] T014 [US1] Rewrite `ToolExecutor.execute()` pipeline in `tooling/executor.py` — tool lookup → `_authorize_tool_call()` → global `trigger_hooks("PreToolUse")` → tool execution → global `trigger_hooks("PostToolUse")` per plan.md Step 5.3
- [x] T015 [US2] Delete `build_tool_executor()` function from `tooling/executor.py` per plan.md Step 5.5; keep `terminal_approver()`
- [x] T016 [US2] Update `tooling/__init__.py` — remove `build_tool_executor` export, add `PermissionGrant` per plan.md Step 6.2
- [x] T017 [US1] Update `main.py` — replace `build_tool_executor(project_root=WORKDIR)` with explicit `engine = create_engine(...)` + `ToolExecutor(permission_engine=engine, approver=terminal_approver)` per plan.md Step 7

**Checkpoint**: `python -c "from tooling.executor import ToolExecutor; from tooling.permission import create_engine; e=create_engine(default_behavior='allow'); t=ToolExecutor(e, lambda n,p,r: {'decision':'allow'}); print('OK')"`

---

## Phase 5: User Story 4 — Session Grants 全量替换 (Priority: P2)

**Goal**: `replace_session_rules(grants)` 原子替换；无效 grant 整体失败保留原状；空列表清空所有规则。

**Independent Test**: 安装 grant A → `replace_session_rules([grant_b])` → 只有 B 生效；无效 grant 抛异常且旧规则保留。

### Implementation

- [x] T018 [US4] Add `replace_session_rules(grants: list[PermissionGrant])` method to `PermissionEngine` in `tooling/permission/engine.py` — build candidates atomically, raise `InvalidPermissionGrant` on any failure per plan.md Step 3.4

**Checkpoint**: `python -c "from tooling.permission import PermissionEngine, PermissionGrant; from tooling.permission.policy import PermissionRule, RuleBehavior; r=PermissionRule('bash', RuleBehavior.ASK, 'rm *', '确认', lambda t,p: True, rule_id='test-1'); e=PermissionEngine([r], default_behavior='deny'); e.replace_session_rules([PermissionGrant('bash','rm *')]); assert e.evaluate('bash',{}).behavior==RuleBehavior.ALLOW; e.replace_session_rules([]); assert len(e._session_rules)==0; print('OK')"`

---

## Phase 6: User Story 5 — Deny 优先级 (Priority: P2)

**Goal**: DENY 规则始终优先于 SESSION ALLOW；即使 session 已授权，DENY 仍然拒绝。

**Independent Test**: Engine 同时有 DENY `(bash, "sudo *")` 和 session ALLOW `(bash, "rm *")` —— `sudo rm -rf /` 被 DENY 拒绝。

### Note

此 Phase 主要为验证。DENY 优先级逻辑已在 Phase 2 (T006 `_collect_all_rules`) 中实现——DENY 规则单独收集，不参与 session grant 覆盖逻辑。

- [x] T019 [US5] Verify deny-priority by running acceptance scenario per quickstart.md VS-4 — logic already implemented in T006 (`_collect_all_rules`), this is validation-only (no code changes)

**Checkpoint**: 确认 `evaluate("bash", {"command": "sudo rm -rf /"})` 返回 DENY，即使 session 中存在其他 bash 授权。

---

## Phase 7: Tests & Polish

**Purpose**: 完整测试覆盖 + 全局验证。

- [x] T020 [P] [US1] Write `TestExecutorIsolation` in `tests/test_permission_engine.py` — test two executors with different engines, session rules isolation per plan.md Step 8.1 US-1
- [x] T021 [P] [US2] Write `TestPermissionNotInGlobalHook` in `tests/test_permission_engine.py` — verify no `register_hook("PreToolUse")` in main.py; verify executor uses internal authorization per plan.md Step 8.1 US-2
- [x] T022 [P] [US3] Write `TestGrantListener` in `tests/test_permission_engine.py` — listener called on new grant, not on replace, fallback raises NonPersistablePermission per plan.md Step 8.1 US-3
- [x] T023 [P] [US4] Write `TestReplaceSessionRules` in `tests/test_permission_engine.py` — full replace, empty clear, atomic rollback, duplicate dedup per plan.md Step 8.1 US-4
- [x] T024 [P] [US5] Write `TestDenyPriority` in `tests/test_permission_engine.py` — deny overrides session allow per plan.md Step 8.1 US-5
- [x] T025 [P] Write `TestPolicyRuleValidation` in `tests/test_permission_engine.py` — duplicate natural key raises, unique keys pass per plan.md Step 8.1 Engine Internal
- [x] T026 Replace `TestPermissionCrossTurn` with `TestPermissionGrantFlow` in `tests/test_conversation.py` — test grant creation via allow_for_session, grant scoped to tool+content per plan.md Step 8.2
- [x] T027 Run full test suite and verify all pass: `D:/Miniconda/envs/llm/python -m pytest tests/ -q`
- [x] T028 Verify no `register_hook("PreToolUse",` remains in production code (grep main.py + tooling/)
- [x] T029 Verify `build_tool_executor` and `create_permission_hook` fully removed from codebase (grep)
- [x] T030 Verify `create_engine` factory still exists and `PermissionRule.rule_id` field preserved (grep `tooling/permission/__init__.py` for `create_engine`, grep `tooling/permission/policy.py` for `rule_id`)

**Checkpoint**: All tests green. No global permission hook. No deleted API remnants. Factory and rule_id preserved.

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundational: engine internal) ← BLOCKS all user stories
    ↓
Phase 3 (US3: Grant/Listener) ← engine public API, prerequisite for executor
    ↓
Phase 4 (US1+US2: Isolation) ← MVP (depends on Phase 3 allow_for_session new signature)
    ↓
Phase 5 (US4: Replace)
    ↓
Phase 6 (US5: Deny Priority) ← can run in parallel with Phase 7 tests
    ↓
Phase 7 (Tests & Polish)
```

### Within Each Phase

- T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008 (sequential, each builds on prior)
- T009 → T010 → T011 (sequential: listener → allow_for_session → delete old)
- T012 → T013 → T014 → T015 → T016 → T017 (sequential: executor init → authorize → execute → delete factory → __init__ → main)
- T018 (standalone)
- T019 (standalone, validation-only)
- T020–T026 (parallel — all different test files/classes)
- T027 depends on T020–T026
- T028, T029, T030 can run any time after Phase 4

### Parallel Opportunities

```
# Phase 7: All test classes can be written in parallel
T020 [US1] TestExecutorIsolation        \
T021 [US2] TestPermissionNotInGlobalHook |
T022 [US3] TestGrantListener             | → T027 → T028 → T029 → T030
T023 [US4] TestReplaceSessionRules       |
T024 [US5] TestDenyPriority              |
T025       TestPolicyRuleValidation      |
T026       TestPermissionGrantFlow       /
```

### User Story Dependencies

| Story | Depends On | Independent Test |
|-------|-----------|------------------|
| US3 (P1) | Phase 2 complete | Listener called exactly once on new grant |
| US1+US2 (P1) | Phase 3 (US3) — `allow_for_session` new signature | Two executors with different engines, B succeeds regardless of A |
| US4 (P2) | Phase 2 + US3 (grant model) | Replace with valid grants → old removed, new active |
| US5 (P2) | Phase 2 (collect logic) | Deny rule blocks even with session allow present |

---

## Implementation Strategy

### MVP First (US1+US2 — Phases 1-4)

1. Complete Phase 1 (T001): `exceptions.py`
2. Complete Phase 2 (T002–T008): Engine internal refactoring
3. Complete Phase 3 (T009–T011): Engine public API (grant/listener) ← **prerequisite**
4. Complete Phase 4 (T012–T017): Executor refactoring + main.py
5. **STOP and VALIDATE**: `python main.py` starts without import errors, `python -c` checkpoint passes
6. This already delivers the core value: no more global hook interference

### Incremental Delivery

1. Phases 1-2 → Foundational ready (engine internals)
2. Phase 3 → US3 done → Grant/listener contract ready for SessionController
3. Phase 4 → US1+US2 done → **MVP: two executors no longer interfere** 🎯
4. Phase 5 → US4 done → Session grants can be restored via `replace_session_rules()`
5. Phase 6 → US5 done → Deny priority verified
6. Phase 7 → Tests green → Ready to merge

### Parallel Team Strategy

With 2 developers after Phase 3:
- Dev A: Phase 4 (US1+US2) → Phase 5 (US4) → Phase 6 (US5)
- Dev B: Phase 7 (Tests) — writes test stubs first, fills in as implementation completes

---

## Notes

- [P] tasks touch different files/classes — safe to run in parallel
- T001–T008 are strictly sequential (each modifies engine.py, building on prior)
- T009–T011 are sequential within engine.py
- T012–T017 are sequential within executor.py → __init__.py → main.py chain
- T020–T026 can all be written simultaneously (different test classes in different files)
- Commit after each phase checkpoint
- No task touches `agent.py`, `conversation.py`, `hooks.py`, or `policy.py`
- Phase 3 (US3) MUST complete before Phase 4 (US1+US2): T013 calls `engine.allow_for_session(result)` new signature created in T010
