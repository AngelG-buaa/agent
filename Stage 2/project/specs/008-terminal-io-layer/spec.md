# Feature Specification: 终端 IO 层

**Feature Branch**: `008-terminal-io-layer`

**Created**: 2026-07-18

**Status**: Draft

**Input**: 终端 IO 层——将项目中散落的 print()/input() 调用统一为一个窄接口的 IOBackend 抽象层，包含 OutputWriter 和 InputReader 协议，让测试可以验证终端输出、为未来 Web UI 铺路。

## User Scenarios & Testing

### User Story 1 - 测试可以验证 Agent 的输出 (Priority: P1)

开发者运行测试时，不需要 mock sys.stdout 或 patch print()，而是通过注入一个 CaptureOutputWriter 来直接断言 Agent 输出了什么。

**Why this priority**: 这是终端 IO 层的核心价值——让输出可测试。目前 TodoWriteTool 和 TerminalApprover 的 print()/input() 无法被测试验证。

**Independent Test**: 创建 IOBackend(output=CaptureOutputWriter, input=FixedInputReader)，注入 Agent，断言 Agent 输出内容被捕获。

**Acceptance Scenarios**:

1. **Given** 一个 CaptureOutputWriter 实例和一个 CaptureInputReader，**When** 将其注入到 Agent 中并运行，**Then** Agent 的所有 print() 输出被写入 CaptureOutputWriter，可断言内容
2. **Given** 一个 CaptureOutputWriter 注入到 TodoWriteTool，**When** TodoWriteTool 打印任务列表，**Then** 输出被捕获而非打印到终端

---

### User Story 2 - 终端交互可测试 (Priority: P1)

开发者测试权限审批流程时，不需要手动输入，而是通过注入一个预设的 FixedInputReader 来模拟用户输入。

**Why this priority**: TerminalApprover 的 input() 调用是单元测试的盲区，目前只能通过集成测试覆盖。

**Independent Test**: 创建 FixedInputReader(["y"])，注入 TerminalApprover，断言批准逻辑按预期执行。

**Acceptance Scenarios**:

1. **Given** FixedInputReader 预设返回 "y"，**When** TerminalApprover 请求用户批准，**Then** ApprovalResult 为批准
2. **Given** FixedInputReader 预设返回 "n"，**When** TerminalApprover 请求用户批准，**Then** ApprovalResult 为拒绝

---

### User Story 3 - 主线代码不直接 print/input，都通过 IOBackend (Priority: P2)

开发者在代码库里 grep print() 时，发现除了 IOBackend 实现本身外，没有其他地方直接调用 print() 或 input()。

**Why this priority**: 这是"单一真相来源"原则——所有终端 IO 都经过 IOBackend。

**Independent Test**: grep 项目源码（排除测试和 IOBackend 本身），确认没有直接 print() / input() 调用。

**Acceptance Scenarios**:

1. **Given** 项目所有模块，**When** 扫描源码（排除 tests/ 目录和 IOBackend 实现），**Then** 没有对 print() / input() 的直接调用
2. **Given** IOBackend 替换为 mock，**When** 运行任一流程，**Then** 无输出写入真实 stderr/stdout

---

### User Story 4 - 未来可替换输出目标（Web UI 等）(Priority: P3)

未来需要支持 WebSocket 推送 Agent 输出时，只需实现一个新的 OutputWriter，不需要改动 Agent 的业务逻辑。

**Why this priority**: 这是抽象层的未来收益，当前不做 Web UI 实现，只留下扩展点。

**Independent Test**: 实现一个 AppendOnlyWriter(list[str]) 并注入，断言 Agent 运行后列表被填充。

**Acceptance Scenarios**:

1. **Given** 一个自定义 OutputWriter 实现，**When** 注入到 Agent 并运行，**Then** Agent 的输出通过该 Writer 输出

### Edge Cases

- InputReader 被多次调用时，read 序列顺序是否正确耗尽
- FixedInputReader 预设列表耗尽后抛出 EOFError
- OutputWriter.write("") 空字符串是否正确处理（不应输出换行）
- 默认终端实现是否保留原始 print/input 的语义（flushing、换行）
- 线程安全：多处同时写 OutputWriter 是否安全（本次不处理）

## Requirements

### Functional Requirements

- **FR-001**: System MUST define an `OutputWriter` protocol/ABC with a `write(text: str) -> None` method
- **FR-002**: System MUST define an `InputReader` protocol/ABC with a `read(prompt: str) -> str` method
- **FR-003**: System MUST provide a `TerminalOutputWriter` that prints to stdout/stderr via print()
- **FR-004**: System MUST provide a `TerminalInputReader` that reads from stdin via input()
- **FR-005**: System MUST provide a `CaptureOutputWriter` that stores output in an append-only list[str] for testing
- **FR-006**: System MUST provide a `FixedInputReader` that returns predefined answers in sequence for testing
- **FR-007**: System MUST provide an `IOBackend` dataclass/container that bundles one OutputWriter + one InputReader + one ToolCallRenderer
- **FR-008**: `Agent` MUST accept an `IOBackend` via constructor parameter (or equivalently via print_handler rename)
- **FR-009**: `TodoWriteTool` MUST accept OutputWriter via constructor parameter, replacing direct print()
- **FR-010**: `TerminalApprover` MUST accept InputReader via constructor parameter, replacing direct input()
- **FR-011**: All existing tests MUST pass without modification (backward compatible: default is TerminalIO)
- **FR-012**: Agent 的构建参数从 `print_handler: Callable[[str], None] | None` 替换为 `io_backend: IOBackend | None`，`print_handler` 的用户改为传入 `io_backend.output`

### Key Entities

- **OutputWriter**: 输出接口，单一方法 `write(text: str)` + 语义方法 `info()`/`warn()`/`error()`/`success()`。负责将文本输出到目标（终端、列表、文件等）
- **InputReader**: 输入接口，单一方法 `read(prompt: str) -> str`。负责从来源读取用户输入
- **ToolCallRenderer**: 工具调用渲染接口，`on_tool_call(name, args)` 替代原 Agent.print_handler 的结构化回调
- **IOBackend**: 数据类容器，组合一个 OutputWriter + 一个 InputReader + 一个 ToolCallRenderer。作为统一的构造参数注入点
- **TerminalOutputWriter**: OutputWriter 的默认实现，使用 print() 输出到终端
- **TerminalInputReader**: InputReader 的默认实现，使用 input() 从终端读取
- **DefaultToolCallRenderer**: ToolCallRenderer 的默认实现，输出 "🔧 调用工具" 信息
- **CaptureOutputWriter**: OutputWriter 的测试实现，将输出追加到 list[str]
- **FixedInputReader**: InputReader 的测试实现，按顺序返回预设的回答列表

## Success Criteria

### Measurable Outcomes

- **SC-001**: 重构后所有现有测试通过，零修改
- **SC-002**: 新增至少 3 个测试用例覆盖 OutputWriter/InputReader 的捕获和模拟场景
- **SC-003**: 项目中所有非 IOBackend 实现本身的 print()/input() 调用被消除
- **SC-004**: 单测中无需 mock sys.stdout / patch print() 即可验证终端输出

## Clarifications

### Session 2026-07-18

- Q: 本次改造的 print()/input() 范围？ → A: 核心三模块——Agent + TodoWriteTool + TerminalApprover。`agent/ui.py`、`agent/conversation.py`、`tools/ask_user.py`、`tools/task.py`、`index_cli.py` 等其余 7 个文件本次不改，留作后续迭代
- Q: IOBackend 如何注入 TodoWriteTool？ → A: 显式构造注入——`TodoWriteTool.__init__` 接受 `OutputWriter`，在 `main.py` 装配时传入同一份 IOBackend 实例
- Q: FixedInputReader 预设耗尽后行为？ → A: 抛出 `EOFError`

## Assumptions

- Agent 的构建参数从 `print_handler: Callable[[str], None] | None` 替换为 `io_backend: IOBackend | None = None`，默认值 = `IOBackend()`（即终端模式）。调用方如 `main.py` 不做改动，沿用默认值
- TerminalOutputWriter 的 `write()` 内部使用 `print(text)`。Agent 现有的 `print_handler` 调用全部改为 `self._io.tool_renderer.on_tool_call(name, args)`
- 不处理 stderr 分离（所有输出暂走统一 write，未来可按需分拆）
- 不处理线程安全（当前所有 IO 在 Agent 单线程中执行，未来需要时再加锁）
- 不处理颜色/ANSI——print() 的 `end=""` 等细节由 TerminalOutputWriter 处理
