---

description: "Task list for terminal IO layer feature"
---

# Tasks: 终端 IO 层

**Input**: Design documents from `specs/008-terminal-io-layer/plan.md`

**Prerequisites**: plan.md (✅), spec.md (✅), research.md (✅), data-model.md (✅)

**Tests**: Core modules (Agent, ToolExecutor) are covered by mandatory test tasks below. Non-core tests are optional.

## Format

- `[P]` = can run in parallel (different files, no dependencies)
- `[US1]`–`[US4]` = which user story this task belongs to
- Include exact file paths

## User Story Map

| Story | Priority | What It Delivers | Independent Test |
|-------|----------|-----------------|------------------|
| US1 | P1 🎯 | Agent 输出可被 CaptureOutputWriter 捕获 | 创建 IOBackend(output=Capture) 注入 Agent，断言输出被捕获 |
| US2 | P1 🎯 | 终端交互可被 FixedInputReader 模拟 | FixedInputReader(["y"]) 注入 TerminalApprover，断言批准 |
| US3 | P2 | 所有 print()/input() 都经 IOBackend | grep 源码（排除 io.py 自身），确认无直接 print/input |
| US4 | P3 | 未来可替换输出目标 | Covered by design — OutputWriter 接口和构造注入机制已实现扩展点 |

---

## Phase 1: Setup (共享基础设施)

**Purpose**: 创建 IOBackend 核心接口和所有默认实现。所有 User Story 依赖于此阶段。

- [X] T001 在 `terminal/io.py` 中定义 OutputWriter(含 info/warn/error/success)、InputReader、ToolCallRenderer ABC
- [X] T003 实现 TerminalOutputWriter / TerminalInputReader / DefaultToolCallRenderer 默认终端实现
- [X] T004 实现 CaptureOutputWriter / FixedInputReader 测试用实现
- [X] T005 实现 IOBackend dataclass + IOBackend.terminal() 工厂方法

**Checkpoint**: `terminal/io.py` 完整可导入。阶段目标：`python -c "from terminal.io import IOBackend; IOBackend.terminal()"` 不报错。

---

## Phase 2: User Story 1 — Agent 输出可测试 (P1) 🎯 MVP

**Goal**: Agent 不再直接调用 print_handler，而是通过 IOBackend.tool_renderer 输出工具调用通知。测试可注入 CaptureOutputWriter 断言输出。

**Independent Test**:
```python
from terminal.io import IOBackend, CaptureOutputWriter, FixedInputReader
cap = CaptureOutputWriter()
io = IOBackend(output=cap, input=FixedInputReader(["y"]),
               tool_renderer=DefaultToolCallRenderer(cap))
agent = Agent(llm=..., executor=..., io_backend=io)
result = agent.run(messages)
assert len(cap.lines) > 0  # 工具调用被捕获
```

### Implementation for User Story 1

- [X] T006 [US1] 修改 Agent.__init__: print_handler 参数替换为 io_backend: IOBackend，内部调用改为 `self._io.tool_renderer.on_tool_call(name, args)`
- [X] T007 [US1] 修改 SubAgent.__init__: 同理使用 io_backend 参数
- [X] T008 [US1] 从 `agent/utils.py` 删除 default_print_handler / sub_print_handler 函数（不再使用）

**Checkpoint**: Agent 构造通过 `io_backend=` 传入 CaptureOutputWriter 后，`_execute_tool_calls` 的输出被捕获。

---

## Phase 3: User Story 2 — 终端交互可测试 (P1) 🎯

**Goal**: TerminalApprover 不再直接调用 input()，而是通过 InputReader.read() 读取用户输入。

**Independent Test**:
```python
from terminal.io import FixedInputReader
reader = FixedInputReader(["y", "n"])
result1 = terminal_approver("bash", {}, "test", input_reader=reader)
assert result1["decision"] == "allow"
```

### Implementation for User Story 2

- [X] T009 [US2] 给 terminal_approver 函数签名增加 `input_reader: InputReader | None = None` 参数
- [X] T010 [US2] terminal_approver 内部所有 `input()` 替换为 `input_reader.read()`，默认使用 TerminalInputReader

**Checkpoint**: TerminalApprover 传入 FixedInputReader 后按预期返回授权/拒绝。

---

## Phase 4: User Story 3 — 消灭直接 print/input (P2)

**Goal**: TodoWriteTool 的 `_print_todos()` 改走 OutputWriter。main.py 装配 IOBackend 实例。

**Independent Test**:
```bash
# grep 确认无遗留
grep -rn "^\s*print\|^\s*input" --include="*.py" tooling/ tools/ agent/ | grep -v io.py | grep -v test_
# 应无输出
```

### Implementation for User Story 3

- [X] T011 [P] [US3] 给 TodoWriteTool.__init__ 增加 `output: OutputWriter | None = None` 参数
- [X] T012 [US3] TodoWriteTool._print_todos() 中 `print()` → `self._output.info()`
- [X] T013 [US3] 修改 `main.py` 创建 IOBackend.terminal() 实例，分别传给 Agent(io_backend=xx)、TodoWriteTool(output=xx.output)、terminal_approver(input_reader=xx.input)
- [X] T014 [US3] grep 验证 `agent/` `tools/` `tooling/` 中无残留的直接 print()/input() 调用（排除 io.py）

**Checkpoint**: main.py 运行 `python main.py` 输出行为不变。grep 无残留。

---

## Phase 5: Polish & 测试

**Purpose**: 回归确认全部现有测试通过，新增 IOBackend 专项测试。

- [X] T015 运行所有现有测试确认零修改通过: `python -m pytest tests/ -q`
- [X] T016 [P] 新增 `tests/test_io_backend.py` — 测试 CaptureOutputWriter / FixedInputReader 基础功能
- [X] T017 [P] 新增测试 — Agent 注入 CaptureOutputWriter 后捕获输出
- [X] T018 [P] 新增测试 — TerminalApprover 注入 FixedInputReader 后模拟交互
- [X] T019 更新 Tech Debt 记录：在 `docs/TECH_DEBT.md` 中将 #2 (缺少统一 IO 抽象层) 标记为已解决

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 Setup ──────────────────────┬────────────────────────────┐
                                    ▼                            ▼
                    Phase 2 [US1] Agent →    Phase 3 [US2] TerminalApprover
                                    │                            │
                                    └──────────┬─────────────────┘
                                               ▼
                                    Phase 4 [US3] TodoWrite + main.py
                                               │
                                               ▼
                                    Phase 5 Polish & Tests
```

- **Phase 1 (Setup)**: 无依赖，先做
- **Phase 2 (US1)**: 依赖 Phase 1
- **Phase 3 (US2)**: 依赖 Phase 1，与 Phase 2 无交叉，可并行
- **Phase 4 (US3)**: 依赖 Phase 1，与 Phase 2/3 无交叉，可在 Phase 1 后随时开始
- **Phase 5 (Polish)**: 依赖 Phase 1–4 完成

### 并行机会

- T003/T004/T005 均在 T001 之后串行，无并行空间（同文件依赖 ABC 定义）
- 本 feature 是串行重构，并行价值低
- Phase 2、3、4 在 Phase 1 完成后可并行
- T016/T017/T018 可并行编写

### MVP Scope

最小可用 = **Phase 1 + Phase 4**（新建 terminal/io.py + main.py 装配 IOBackend，输出行为不变）
但核心价值（可测试的 US1、US2）要到 Phase 2、3 才交付。

---

## Implementation Strategy

1. **Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5** (串行，适合单人开发)
2. 每完成一个 Phase 提议 git commit
3. 每次 Phase 完成时运行 `python -m pytest tests/ -q` 确保回归
