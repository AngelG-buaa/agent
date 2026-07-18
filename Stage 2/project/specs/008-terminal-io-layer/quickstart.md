# Quickstart: 终端 IO 层

> 快速验证 IOBackend 抽象层是否正确工作。

## 前提条件

- Python 3.12+
- 项目环境已配置（`D:/Miniconda/envs/llm/python`）
- pytest

## 验证场景

### 1. IOBackend 默认终端模式可用

```bash
cd Stage 2/project
D:/Miniconda/envs/llm/python -c "
from terminal.io import IOBackend, TerminalOutputWriter, TerminalInputReader

io = IOBackend()
# 默认输出到终端
io.output.write('IOBackend 默认模式: 这行应该出现在终端')
"
```

**预期**: 屏幕输出 "IOBackend 默认模式: 这行应该出现在终端"

---

### 2. CaptureOutputWriter 捕获输出

```bash
D:/Miniconda/envs/llm/python -c "
from terminal.io import CaptureOutputWriter

cap = CaptureOutputWriter()
assert cap.lines == []

cap.write('Hello')
cap.write('World')
assert cap.lines == ['Hello', 'World'], f'got {cap.lines}'

cap.clear()
assert cap.lines == []

print('✅ CaptureOutputWriter 工作正常')
"
```

**预期**: 输出 "✅ CaptureOutputWriter 工作正常"

---

### 3. FixedInputReader 模拟输入

```bash
D:/Miniconda/envs/llm/python -c "
from terminal.io import FixedInputReader

reader = FixedInputReader(['y', 'n'])
assert reader.read('') == 'y'
assert reader.read('') == 'n'
assert reader.remaining == []

try:
    reader.read('')
    assert False, '应该抛出 EOFError'
except EOFError:
    print('✅ FixedInputReader 耗尽时抛出 EOFError')
"
```

**预期**: 输出 "✅ FixedInputReader 耗尽时抛出 EOFError"

---

### 4. Agent 使用 IOBackend

```bash
D:/Miniconda/envs/llm/python -c "
from terminal.io import IOBackend, CaptureOutputWriter, FixedInputReader
from agent.agent import Agent

cap = CaptureOutputWriter()
io = IOBackend(
    output=cap,
    input=FixedInputReader(['y']),
    tool_renderer=None,  # 测试中不需要渲染工具调用
)
agent = Agent(llm=None, executor=None, io_backend=io)
assert agent._io is io
print('✅ Agent 接受 IOBackend 构造参数')
"
```

**预期**: 输出 "✅ Agent 接受 IOBackend 构造参数"

---

### 5. 语义方法 info/warn/error/success 可覆盖

```bash
D:/Miniconda/envs/llm/python -c "
from terminal.io import CaptureOutputWriter

cap = CaptureOutputWriter()
cap.info('info msg')
cap.warn('warn msg')
cap.error('error msg')
cap.success('success msg')
assert len(cap.lines) == 4  # 全部捕获
print('✅ 语义方法默认可工作:', cap.lines)
"
```

**预期**: 输出 "✅ 语义方法默认可工作: ['info msg', '⚠️  warn msg', '❌  error msg', '✅  success msg']"

---

### 6. 全部现有测试通过

```bash
D:/Miniconda/envs/llm/python -m pytest tests/ -q
```

**预期**: 所有测试 PASS（零修改）
