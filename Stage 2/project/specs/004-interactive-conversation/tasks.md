# Tasks: 交互式对话

**Input**: Design documents from `specs/004-interactive-conversation/`

**Prerequisites**: plan.md, spec.md (user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Constitution Principle VII — Agent 循环逻辑、Tool 执行管线、权限引擎为"核心模块"，测试强制。Conversation（agent 编排层）和 AskUserTool（工具执行管线）均属核心模块，必须有测试覆盖。

**Organization**: Tasks grouped by user story (US1/US2/US3)，每个 story 独立可测。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属 user story（US1, US2, US3）
- 每条含确切的文件路径

---

## Phase 1: Setup

**Purpose**: 建立基线，确认无回归

- [x] T001 运行现有全部测试（`python -m pytest tests/ -q`），确认 99 tests 全部通过后再开始改动

---

## Phase 2: Foundational — Agent.run() 签名变更

**Purpose**: `Agent.run()` 从 `run(user_input: str)` 改为 `run(messages: list[dict])`，并更新所有现有调用方。这是 P1/P2/P3 的前置条件，必须先完成。

**⚠️ CRITICAL**: T002–T005 必须一起完成——`run()` 签名变更后所有调用方编译失败，无法增量提交。

- [x] T002 修改 `Agent.run()` 签名：`run(self, messages: list[dict]) -> str`，删除方法内前 3 行（messages 创建 + system prompt + user 插入），循环体不动。文件：`agent/agent.py`
- [x] T003 [P] 更新 `spawn_subagent()` 适配新签名——组装 `[sub_system_msg, user_msg]` 传入 `sub.run(messages)`。文件：`tools/task.py`
- [x] T004 [P] 更新 `test_task.py` 中所有 `agent.run("input")` 为 `agent.run([sys_msg, user_msg])`。文件：`tests/test_task.py`
- [x] T005 [P] 检查并更新 `test_todo_write.py` 中如有 `Agent.run()` 调用，同样适配。文件：`tests/test_todo_write.py`

**Checkpoint**: `python -m pytest tests/ -q` 全部通过。Agent.run() 现在接收 messages 列表，所有现有调用方已适配。

---

## Phase 3: User Story 1 — 多轮连续对话 (Priority: P1) 🎯 MVP

**Goal**: 用户启动 Agent 后可以连续进行多轮对话，Agent 记住上下文。输入 `/exit` 或 Ctrl+C 退出。

**Independent Test**: 启动 Agent → "帮我创建 hello.py" → 创建完成 → "给它加上 main 函数" → Agent 理解"它"=hello.py → `/exit` 退出。

### Tests for User Story 1 ⚠️

> **NOTE**: Conversation 涉及 agent 编排（核心模块），测试强制。

- [x] T006 [P] [US1] 编写 Conversation 单元测试：首轮插入 system prompt、后续轮复用 messages、空输入忽略、`/exit` 退出、Ctrl+C 中断（mock `input()`）。文件：`tests/test_conversation.py`

### Implementation for User Story 1

- [x] T007 [US1] 创建 `Conversation` 类：`start()` 外循环 + `_run_turn()` 组装 messages 并调用 `agent.run(self.messages)`。包含：`if not self.messages` 判断首轮插入 system prompt、空输入跳过、`/exit` 拦截、Ctrl+C 连续中断保护、API 异常兜底 (`except Exception`)。文件：`agent/conversation.py`
- [x] T008 [US1] 更新 `main.py`：删除硬编码 `question` 和 `agent.run(question)`，改为 `Conversation(agent).start()`。文件：`main.py`

**Checkpoint**: `python main.py` 启动后可以多轮交互，`/exit` 退出。`pytest tests/test_conversation.py` 通过。

---

## Phase 4: User Story 2 — Agent 反问用户 (Priority: P2)

**Goal**: Agent 在执行过程中遇到歧义时，调用 `ask_user` 工具向用户提问，用户回答后继续执行。

**Independent Test**: 给模糊指令"帮我改一下那个文件"→ Agent 反问"哪个文件？"→ 用户指定 → Agent 修改正确文件。

### Tests for User Story 2 ⚠️

> **NOTE**: AskUserTool 属于工具执行管线（核心模块），测试强制。

- [x] T009 [P] [US2] 编写 AskUserTool 单元测试：正常问答、空问题参数、Ctrl+C 跳过、空回答、"不知道/随便"返回 is_valid=false。文件：`tests/test_ask_user.py`

### Implementation for User Story 2

- [x] T010 [US2] 创建 `AskUserTool`：继承 `Tool` ABC，`run()` 调用 `input()` 阻塞等待，返回 `{"answer": "...", "is_valid": true/false}`。含无效回答检测（"不知道"/"随便"/"你自己决定" 等 → `is_valid: false`）。文件：`tools/ask_user.py`
- [x] T011 [P] [US2] 在 `register_all()` 中注册 `AskUserTool`。文件：`tools/__init__.py`
- [x] T012 [P] [US2] 将 `"ask_user"` 加入 `_SAFE_TOOLS` 列表，自动生成 ALLOW 权限规则。文件：`tooling/permission/policy.py`
- [x] T013 [US2] 验证 SYSTEM_PROMPT 已包含"自主决策 vs 反问用户"和"提问规范"——prompt 已在 spec 阶段重设计并写入 `agent/prompts.py`，此 task 确认内容正确并做最终调优。文件：`agent/prompts.py`

**Checkpoint**: 用模糊指令测试 → Agent 反问 → 回答后继续完成。`pytest tests/test_ask_user.py` 通过。

---

## Phase 5: User Story 3 — 对话状态保持 (Priority: P3)

**Goal**: 权限"始终允许"和 TodoWrite 任务列表在多轮对话中跨轮保持。

**Independent Test**: 第 N 轮选择"始终允许读文件" → 第 N+1 轮同类操作不再询问；第 N 轮 TodoWrite 有 3 个未完成任务 → 第 N+1 轮仍可见。

### Verification for User Story 3

> **NOTE**: 此 story 主要是验证现有机制在跨轮场景下工作正常，而非新增代码。PermissionEngine 的 `_session_rules` 和 `CURRENT_TODOS` 都已是进程级状态，自然跨轮保持。

- [x] T014 [P] [US3] 编写权限跨轮保持测试：在两轮对话中触发同一工具调用，验证首轮的"始终允许"在第二轮生效（不再弹权限提示）。文件：`tests/test_conversation.py`（追加）
- [x] T015 [US3] 编写 TodoWrite 跨轮保持 + compact 恢复测试：模拟多轮对话后触发 L4 compact，验证 `_restore_todos` 正确恢复任务列表。文件：`tests/test_conversation.py`（追加）

**Checkpoint**: `pytest tests/test_conversation.py -k "permission or todo"` 通过。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 端到端验证、回归测试、清理

- [x] T016 按 [quickstart.md](./quickstart.md) 的 6 个场景手动验证端到端行为
- [x] T017 运行完整测试套件 `python -m pytest tests/ -q`，确认全部通过且无回归
- [x] T018 检查 `agent/agent.py` 循环体与方法签名——确认无遗留的 `user_input` 参数引用、注释与代码一致

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup: baseline)
  │
  ▼
Phase 2 (Foundational: run() 签名变更)  ← 阻塞所有 User Story
  │
  ├──► Phase 3 (US1: 多轮对话)         ← P1 MVP
  │       │
  │       ├──► Phase 5 (US3: 状态保持)  ← 依赖 US1 的 Conversation
  │
  └──► Phase 4 (US2: 反问工具)         ← P2，可与 US1 并行
         │
         └──► (US2 独立于 US1，可先于/后于/并行于 US1)
  │
  ▼
Phase 6 (Polish: 端到端验证)
```

### User Story Dependencies

| Story | 前置依赖 | 可否并行 |
|-------|---------|---------|
| US1 (多轮对话) | Phase 2 完成 | 可与 US2 并行 |
| US2 (反问工具) | Phase 2 完成 | 可与 US1 并行 |
| US3 (状态保持) | Phase 2 + US1 完成 | 不可与 US1 并行（依赖 Conversation） |

### Within Each Phase

- US1: T006 (tests) 可与 T007 并行写 → T008 最后
- US2: T009 (tests) 可与 T010 并行写 → T011、T012 可并行 → T013 最后
- US3: T014、T015 可并行

---

## Parallel Example: US2

```bash
# 三件事可以同时开始：
Task: "T009 创建 AskUserTool 单元测试在 tests/test_ask_user.py"
Task: "T010 创建 AskUserTool 在 tools/ask_user.py"
Task: "T012 添加 ask_user 到 _SAFE_TOOLS 在 tooling/permission/policy.py"
# T011 等 T010 完成后注册
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Phase 1: 跑 baseline 测试 (T001)
2. Phase 2: 改 `run()` 签名 + 适配现有调用方 (T002–T005)
3. Phase 3: Conversation + main.py + 测试 (T006–T008)
4. **STOP & VALIDATE**: `python main.py` 多轮交互 → `/exit` 退出
5. **MVP 交付**：用户已可连续对话

### Incremental Delivery

1. Setup + Foundational → 基础就绪
2. + US1 → 多轮对话可用 **(MVP!)**
3. + US2 → Agent 可反问用户
4. + US3 → 权限/Todo 跨轮保持验证通过
5. + Polish → 端到端验证完成

### File Change Map

| Task | File | 类型 |
|------|------|------|
| T002 | `agent/agent.py` | 修改（~3 行） |
| T003 | `tools/task.py` | 修改（~5 行） |
| T004 | `tests/test_task.py` | 修改（适配签名） |
| T005 | `tests/test_todo_write.py` | 修改（如需要） |
| T006 | `tests/test_conversation.py` | 新增 |
| T007 | `agent/conversation.py` | 新增（~70 行） |
| T008 | `main.py` | 修改（~15 行） |
| T009 | `tests/test_ask_user.py` | 新增 |
| T010 | `tools/ask_user.py` | 新增（~55 行） |
| T011 | `tools/__init__.py` | 修改（+2 行） |
| T012 | `tooling/permission/policy.py` | 修改（+1 行） |
| T013 | `agent/prompts.py` | 验证（已写入） |
| T014–T015 | `tests/test_conversation.py` | 追加 |
| T016–T018 | 无文件改动 | 手动验证 |

**总计**: 4 个新文件，6 个修改文件，1 个验证文件。核心代码 ~150 行。

---

## Notes

- [P] 任务 = 不同文件，无依赖，可并行
- T002–T005 必须一起提交（签名变更 + 调用方适配 = 原子操作）
- 所有 `Agent.run()` 的 `user_input: str` 参数引用在 Phase 2 后不应存在于代码库中
- Constitution Principle VII: T006 和 T009 是强制测试，不可跳过
- 每完成一个 Phase 就跑 `pytest` 确认无回归
