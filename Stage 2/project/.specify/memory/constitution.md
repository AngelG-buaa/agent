<!--
  ============================================================================
  Sync Impact Report
  ============================================================================
  Version change: 0.0.0 → 1.0.0 (initial constitution)
  Modified principles: N/A (first version)
  Added sections:
    - Core Principles (8 principles)
    - Code Quality Standards
    - Technical Constraints
    - Delivery & Workflow
    - Governance
  Removed sections: N/A
  Templates requiring updates:
    - .specify/templates/plan-template.md       ✅ aligned (Constitution Check gate exists)
    - .specify/templates/spec-template.md       ✅ aligned (no conflicts)
    - .specify/templates/tasks-template.md      ✅ updated (test requirement aligned with Principle VII)
    - .specify/templates/checklist-template.md  ✅ aligned (no conflicts)
  Follow-up TODOs: None
  ============================================================================
-->

# myAgentProject Constitution

## Core Principles

### I. Correctness First, Then Optimize

功能正确永远是第一优先级。宁可代码朴素但正确，不要精巧但有 bug。

- 新功能的实现顺序：**先跑通正确路径 → 再处理错误分支 → 最后才考虑性能优化**
- 优化必须有明确的瓶颈证据（profiling、benchmark），不做猜测性优化
- 代码审查时，正确性缺陷为最高优先级，block merge

### II. Small Steps, Fast Iteration

小步快跑，每步可验证。拒绝大 PR、大设计、大重构。

- 每个 PR 只做一件事，保持小而可审查（reviewable）
- 每次迭代有明确的"完成标准"：一个可演示、可测试的状态
- 如果发现自己在 3 个以上不相关的文件间跳转，停下来——范围可能过大了

### III. Clarity & Maintainability

代码是写给人读的，其次才是给机器执行的。

- 命名必须自解释，不用缩写除非是领域通用术语（如 `llm`、`rag`）
- 复杂逻辑（正则、算法、状态机、嵌套 > 2 层的条件/循环）必须注释说明 **"为什么这么做"**，而非"做了什么"
- 每个模块能用一句话说清它的单一职责；说不清时就该拆

### IV. Good Architecture, Consistent Style

架构分层清晰，风格保持一致。

- 保持与现有代码相同的风格：dataclass、type hints、docstring 格式、命名约定
- 新代码必须遵循现有的分层哲学（参考 `docs/architecture-philosophy.md`）
- 模块间单向依赖，禁止循环引用
- 通过工厂函数组装组件，避免全局单例

### V. Don't Reinvent the Wheel

优先使用现有工具和框架。

- 标准库能做的事，不引入第三方库
- 已有的成熟方案（框架、模式、库）优于从零手写
- 引入新依赖需在 spec 阶段评估并记录理由
- 参考但不盲从——理解后再用，不 cargo cult

### VI. Align with Mainstream Practices

做法要与社区主流实践保持一致，不异想天开。

- 设计模式、项目结构、API 风格优先参考社区公认的 best practices
- 如果发现自己在做一件"独特"的事情，停下来——问问是否有更常规的做法
- 技术选型优先选择活跃维护、文档完善、社区广泛的方案

### VII. Core Modules Must Have Unit Tests

核心模块必须有单测覆盖。

- "核心模块"定义：Agent 循环逻辑、Tool 执行管线、权限引擎、RAG 管线
- 测试必须在 PR 合并前通过
- 测试用例覆盖：正常路径 + 关键边界条件 + 已知错误路径
- 不要求 TDD，但鼓励"先写测试再生产代码"的方式

### VIII. Backward Compatibility Awareness

不随意破坏已有接口，保持功能分工明确。

- 已有的公开接口（如 `Agent.run()`、Tool 基类、Hook 注册机制）尽量保持稳定
- 重构时先理解现有的功能边界，不跨边界改写
- 如果确实需要 breaking change，必须在 spec 阶段提出并与 reviewer 讨论

## Code Quality Standards

### Error Handling

- **边界层统一处理**：API 入口、CLI 入口、工具执行入口等边界层负责捕获异常并转为用户友好的响应
- **内部模块自然传播**：业务逻辑内部不吞异常，让异常自然向上传播到边界层
- 禁止 `except: pass` 或 `except Exception: pass` 式的静默吞异常

### Documentation

- **设计决策记录 (ADR)**：重要的设计决策（如选择 FAISS 而非 Qdrant、选择同步而非异步）必须写 ADR 记录在 `docs/` 目录下
- 复杂逻辑必须有注释解释"为什么这么做"
- 公开 API（模块入口、工厂函数、基类）必须有 docstring

### Code Style

- 严格保持与现有代码一致的风格（参考项目现有的 dataclass、type hints、命名约定）
- 遵循 PEP 8，使用 type hints
- 命名：类用 PascalCase，函数/变量用 snake_case，常量用 UPPER_SNAKE_CASE

## Technical Constraints

### Language & Runtime

- **必须使用 Python**（3.12+）
- 其他技术选型灵活，在 spec 阶段决定

### Performance

- 无硬性性能指标要求
- 功能正确优先于性能优化
- 后期如需支持流式输出，作为独立功能迭代

### Security

- 无额外硬性安全要求
- 现有权限系统（`tooling/permission/`）继续维护，作为安全兜底

## Delivery & Workflow

### Iteration Strategy

1. Spec 先行：每个功能迭代从 spec 编写开始，用户深度介入规格设计
2. Plan 确认：技术方案需在 plan 中明确，通过 Constitution Check
3. 小 PR 交付：每个 PR 只做一件事，diff 尽量 < 300 行
4. 测试通过：PR 合并前必须通过所有测试

### Merge Standards

- 所有测试必须通过
- 代码需用户 review 确认
- 不允许合并已知有 bug 的代码（除非 bug 本身是另一张独立的 ticket）

### Constitution Check

每个 feature 的 plan 阶段必须通过以下检查：

1. 是否符合"先正确再优化"？（没有不必要的优化）
2. 是否保持了现有架构分层？新代码落点是否在正确的模块？
3. 是否保持了现有代码风格？
4. 是否有轮子被重复发明？（技术上是否有更简单的方案？）
5. 设计是否与主流做法一致？
6. 核心模块是否有测试计划？
7. 是否引入了不必要的 breaking change？

## Governance

本 Constitution 是项目开发的最高准则。所有 spec、plan、PR 都应与此对齐。

- **修订流程**：提出修订 → 讨论 → 更新 Constitution → 同步受影响的模板和文档
- **版本策略**：MAJOR.MINOR.PATCH（语义化版本），参考 sync impact report
- **合规审查**：每次 spec + plan 阶段通过 Constitution Check gate

**Version**: 1.0.0 | **Ratified**: 2026-07-11 | **Last Amended**: 2026-07-11
