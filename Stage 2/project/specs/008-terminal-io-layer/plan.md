# Implementation Plan: 终端 IO 层

**Branch**: `main` (no new branch) | **Date**: 2026-07-18 | **Spec**: [spec.md](../spec.md)

**Input**: Feature specification from `specs/008-terminal-io-layer/spec.md`

## Summary

将项目中散落在 Agent（print_handler）、TodoWriteTool（`_print_todos()` 的 `print()`）、TerminalApprover（`input()`）三处的终端 IO 调用统一为一个 IOBackend 窄接口抽象层。通过构造参数注入的方式替换直接 print()/input()，核心三模块改造完成后测试可以通过注入 CaptureOutputWriter / FixedInputReader 来验证终端输出和交互，不再需要 mock sys.stdout / patch print()。

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: 无新依赖（标准库 typing.Protocol / abc.ABC 即可）

**Storage**: N/A（纯接口抽象，不涉及持久化）

**Testing**: pytest 已有，增加对 OutputWriter/InputReader 的测试

**Target Platform**: 终端 / CI

**Project Type**: CLI 工具（Agent 框架），重构

**Performance Goals**: 无（print/input 本身已是 IO 瓶颈，抽象层无额外开销）

**Constraints**: 所有现有测试零修改通过；main.py 的装配代码不做改动

**Scale/Scope**: 3 个模块（Agent + TodoWriteTool + TerminalApprover），~80 行新增代码

**未来方向**: OutputWriter 预留了 `info()`/`warn()`/`error()`/`success()` 语义方法作为挂钩，新增 `ToolCallRenderer` 接口结构化渲染工具调用。当前默认实现的行为等价于原 print()；未来 Claude Code CLI 式界面可覆盖这些方法做 stderr 分流、颜色染色、进度条更新等。

## 架构设计审查（对照《架构设计哲学》）

### 高内聚低耦合（首要判据）

- **波及面**：未来想把终端输出改为 Web UI 时，只需要实现一个新的 OutputWriter，改动只涉及 `main.py` 的装配代码（改传入的 IOBackend）。其他模块不需要感知。—— **理想答案是 1，通过 ✅**
- **接口宽度**：`OutputWriter.write(text: str)` 和 `InputReader.read(prompt: str) -> str` 是极窄接口。消费者只需要知道这两个方法签名。—— **窄接口 ✅**
- **依赖方向**：IOBackend 被 `agent/` 和 `tooling/` 消费，IOBackend 不反向依赖任何业务模块。—— **单向依赖 ✅**
- **一句话职责**：IOBackend = "统一的终端 IO 访问点"。清晰单一。—— **通过 ✅**

### 与主流一致

- 定义 ABC/Protocol + 默认实现 + 注入是 Python 社区的标准做法（logging.Handler、io.IOBase 都是这个模式）。—— **通过 ✅**
- 遵循本项目的现有分层：IOBackend 定义在 `tooling/` 层（基础设施），与 `Tool` 基类同级。—— **通过 ✅**

### 对扩展开放，对修改关闭

- 新增输出目标（文件、WebSocket）只需要新增一个 OutputWriter 实现，不需要修改 Agent/TodoWriteTool/TerminalApprover。—— **通过 ✅**
- InputReader 同理。

### 模块边界即知识边界

- Agent 不直接知道 IOBackend 的默认实现是什么（`TerminalOutputWriter` 在构造时注入，Agent 只看到 `OutputWriter` 接口）。—— **通过 ✅**

### 参数过长是概念未内聚的信号

- IOBackend 只是一个 OutputWriter + InputReader 的容器，参数不多。—— **N/A ✅**

### 架构坏味道扫描

| # | 信号 | 检查结果 |
|---|------|---------|
| 1 | 配置散落：同一组参数在多个位置传递 | ✅ 无，IOBackend 在 main.py 构造一次后传入 Agent 和 TodoWriteTool |
| 2 | 生命周期复杂化：依赖全局注册/注销 | ✅ 无，纯构造注入，无需 dispose |
| 3 | 侵入已有核心逻辑：为适应新功能改核心流程 | ✅ Agent.run() 不改，只改初始化和内部 print_handler 调用点 |
| 4 | 重复判断逻辑：同一检查出现两次 | ✅ 无 |
| 5 | "独特"做法 | ✅ Python 社区标准做法 |
| 6 | 大面积触碰历史代码 | ✅ 只改核心三模块，不改其他 7 个文件 |

**结论：架构设计干净，无坏味道。**

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | 符合"先正确再优化"？ | ✅ 新增测试验证原有逻辑零修改通过，重构不引入新功能 |
| 2 | 保持了现有架构分层？新代码落点正确？ | ✅ OutputWriter/InputReader/IOBackend 落点 `terminal/io.py`，与 agent/tools/tooling 平级 |
| 3 | 保持现有代码风格？ | ✅ dataclass + ABC + type hints |
| 4 | 重复发明轮子？ | ✅ 不引入任何新依赖 |
| 5 | 与主流做法一致？ | ✅ |
| 6 | 核心模块有测试计划？ | ✅ 新增 test_io_backend.py |
| 7 | 不必要的 breaking change？ | ✅ main.py 的构造代码完全兼容（默认值不动） |
| 8 | 架构坏味道？(Principle X) | ✅ 扫描全部通过 |
| 9 | 继承合理性？ | N/A 无继承 |

**Gate: ✅ PASS**

## Project Structure

### Documentation (this feature)

```text
specs/008-terminal-io-layer/
├── spec.md              # 功能规格（已存在）
├── plan.md              # 本文件
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
└── quickstart.md        # Phase 1 输出
```

### Source Code (repository root)

```text
# IOBackend 定义
terminal/
├── __init__.py            # ★ 新建
└── io.py                  # ★ 新增：OutputWriter, InputReader, ToolCallRenderer, IOBackend, 默认实现

# 工具基础设施（不变）
tooling/
├── __init__.py
├── base.py
├── registry.py
├── executor.py            # TerminalApprover → 注入 InputReader
└── permission/

# Agent 层
agent/
├── agent.py               # print_handler → io_backend
└── ...                    (不变)

# 工具层
tools/
├── todo_write.py        # print() → OutputWriter
└── ...                  (不变)

# 测试
tests/
├── test_io_backend.py   # ★ 新增
└── ...                  (不变)
```

**Structure Decision**: 单项目布局。新接口放在 `tooling/` 层，因为它是基础设施（与 `Tool` 基类同级），不依赖 Agent/Tools 层。

## Complexity Tracking

> 无 Constitution 违规，本表留空。

## 设计自查

### 核心实体

详见 [data-model.md](data-model.md)

| 实体 | 方法 | 职责 |
|------|------|------|
| `OutputWriter` (ABC) | `write`, `info`*, `warn`*, `error`*, `success`* | 输出抽象（带语义挂钩） |
| `InputReader` (ABC) | `read(prompt: str) -> str` | 输入抽象 |
| `ToolCallRenderer` (ABC) | `on_tool_call(name, args)`, `on_tool_result(name, result)` | 工具调用渲染（替代 print_handler） |
| `IOBackend` (dataclass) | output, input, tool_renderer | 统一容器 |
| `TerminalOutputWriter` | 继承 OutputWriter | 终端输出（print） |
| `TerminalInputReader` | 继承 InputReader | 终端输入（input） |
| `DefaultToolCallRenderer` | 继承 ToolCallRenderer | 默认终端工具调用渲染 |
| `CaptureOutputWriter` | 继承 OutputWriter | 测试捕获 |
| `FixedInputReader` | 继承 InputReader | 测试模拟 |

> *info/warn/error/success 不是 abstract——默认委托给 write()，子类按需覆盖。

### 任务分解（→ tasks.md）

**Phase 1 — IOBackend 核心实现** (预期 diff ~60 行)
1. 在 `terminal/io.py` 中定义 OutputWriter / InputReader / ToolCallRenderer ABC
2. 给 OutputWriter 添加 info/warn/error/success 默认实现（委托给 write）
3. 实现 TerminalOutputWriter / TerminalInputReader / DefaultToolCallRenderer
4. 实现 CaptureOutputWriter / FixedInputReader
5. 实现 IOBackend dataclass + IOBackend.terminal() 工厂方法

**Phase 2 — Agent 改造** (预期 diff ~25 行)
6. Agent.__init__: print_handler 参数替换为 io_backend: IOBackend
7. _execute_tool_calls(): self.print_handler(name, args) → self._io.tool_renderer.on_tool_call(name, args)
8. SubAgent.__init__: 同理使用 io_backend

**Phase 3 — TodoWriteTool 改造** (预期 diff ~15 行)
9. TodoWriteTool.__init__: 新增 output: OutputWriter 参数
10. _print_todos(): print() → self._output.info()

**Phase 4 — TerminalApprover 改造** (预期 diff ~20 行)
11. terminal_approver: 新增 input_reader: InputReader 参数
12. 所有 input() → input_reader.read()

**Phase 5 — main.py 装配** (预期 diff ~15 行)
13. 创建 IOBackend.terminal() 实例
14. 传给 Agent、TodoWriteTool、terminal_approver

**Phase 6 — 测试** (预期 diff ~50 行)
15. 新增 test_io_backend.py，覆盖 OutputWriter/InputReader/ToolCallRenderer 的捕获和模拟
16. 运行全部测试确认零修改通过

### 关键设计决策

1. **为什么用 ABC 而非 Protocol？** — Protocol 在 Python 3.12+ 的 `@runtime_checkable` 有限制（不能检查 `__init__`），ABC 更明确且本项目的工具基类也用 ABC，保持风格一致。
2. **为什么 output 要加 info/warn/error/success 语义方法？** — 为 Claude Code CLI 式界面预留挂钩。默认行为等价于 write()，未来 UI 可覆盖 info/error 走 stderr、success 走 stdout，测试实现则全量捕获无区分。
3. **为什么新增 ToolCallRenderer？** — Agent 的 print_handler 是一个结构化回调（接收 name + args 而非纯文本），用单独接口承载比塞进 OutputWriter 更清晰。这使未来 UI 可以独立渲染工具调用进度（spinner）+ 工具结果（折叠/颜色），与文本输出解耦。
4. **为什么不用 typing.IO / contextlib.redirect_stdout？** — typing.IO 是字节/文件流的抽象，不是终端 IO 级别的抽象。redirect_stdout 是运行时全局替换，不是构造注入——测试时需要上下文管理器生命周期，比构造注入更重。redirect_stdout 还不能分离 stdout/stderr。
