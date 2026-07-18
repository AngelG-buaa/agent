# Research: 终端 IO 层

## 未决项扫描

Technical Context 中无 `NEEDS CLARIFICATION` 标记，无未决 unknowns。

## 技术选型决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 接口类型 | ABC (abc.ABC) | 项目中 Tool 基类使用相同模式，保持风格一致；支持 `isinstance()` 检查 |
| 默认实现 | print()/input() | 零改动。TerminalOutputWriter.write(text) 等价于 print(text) |
| 容器方式 | dataclass | 轻量、不可变、类型安全，与项目的 config.py 风格一致 |
| 测试实现 | CaptureOutputWriter + FixedInputReader | 直接断言列表内容，无 mock 依赖 |
| 文件位置 | terminal/io.py | 独立目录，与 agent/tools/tooling 平级 |

## 备选方案评估

- `typing.Protocol` → 被否决，因为 `@runtime_checkable` 不支持检查 `__init__` 签名
- `contextlib.redirect_stdout` → 被否决，运行时全局替换而非构造注入，测试需要上下文管理器
- 单文件存放 vs 独立包 → 单文件（不足 100 行，拆分 package 反而过度工程）
