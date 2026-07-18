# Data Model: 终端 IO 层

> 本 feature 不涉及持久化数据，以下定义的是内存中的接口和实现。

## 设计约束：为 Claude Code CLI 式界面预留扩展点

这层 IO 的设计不只是为了替换 print()/input()——它的长远目标是让项目能接入类似 Claude Code CLI 的界面：

| Claude Code CLI 特征 | 当前输出方式 | IO 层如何支持 |
|---|---|---|
| 状态/进度走 stderr，结果走 stdout | 全 stdout 混合 | 语义方法 (`info`/`error`/`success`) 给 UI 挂钩，判断渲染到哪个流 |
| 工具调用可视化 (`🔧 调用工具`) | print_handler 直接 print | IOBackend 接管，未来可在 `on_tool_call()` 回调里做格式化 |
| 消息带层级/emoji | 代码里硬编码 | `info()`/`warn()`/`error()`/`success()` 方法可分别染色 |

本次迭代只做抽象层，不实现 Claude Code CLI 界面。但 **接口本身是面向这个目标设计的**。

## 实体定义

### OutputWriter (ABC)

```python
class OutputWriter(ABC):
    """输出抽象 —— 负责将文本输出到目标（终端、内存列表、文件等）。
    
    本次迭代只使用 write() 方法。info/warn/error/success 是语义挂钩，
    留给未来 Claude Code CLI / Web UI 等实现做差异化渲染。
    """
    
    @abstractmethod
    def write(self, text: str) -> None:
        """输出一段文本。所有语义方法的默认下落点。"""
        ...
    
    # ── 语义方法（未来挂钩）────────────────────────────────
    # 默认行为 = 委托给 write()。子类按需覆盖：
    #   普通终端         → write(text) → print(text) → stdout
    #   Claude Code CLI → info/error 走 stderr, success 走 stdout
    #   Web UI          → 按 level 渲染不同组件
    #   Capture测试     → 全部收集到列表，不区分流
    
    def info(self, text: str) -> None:
        """一般信息。"""
        self.write(text)
    
    def warn(self, text: str) -> None:
        """警告信息。"""
        self.write(f"⚠️  {text}")
    
    def error(self, text: str) -> None:
        """错误信息。"""
        self.write(f"❌  {text}")
    
    def success(self, text: str) -> None:
        """成功/完成信息。"""
        self.write(f"✅  {text}")
```

**约束**:
- `write("")` 不得产生任何输出（空字符串 = no-op）
- `write` 默认实现负责添加换行（`print(text)` 天然带换行）
- 语义方法的 emoji 前缀由**实现方**选择添加——当前 TerminalOutputWriter 的默认实现加上 emoji 以保持与现有输出一致；CaptureOutputWriter 捕获原始文本不加处理

---

### ToolCallRenderer (ABC) — 本次预留，为空接口

```python
class ToolCallRenderer(ABC):
    """工具调用渲染回调 —— 结构化地接收工具调用事件。
    
    本次迭代的 print_handler 被 IOBackend 接管后，Agent 通过此接口
    发送工具调用通知。默认实现直接 write；未来 Claude Code CLI 可
    覆盖为带 spinner/时间的格式化输出。
    """
    
    @abstractmethod
    def on_tool_call(self, name: str, args: dict) -> None:
        """工具即将被调用时触发。"""
        ...
    
    @abstractmethod
    def on_tool_result(self, name: str, result: dict) -> None:
        """工具执行完成时触发。"""
        ...


class DefaultToolCallRenderer(ToolCallRenderer):
    """默认终端渲染 —— 等价于当前 print_handler 的打印行为。"""
    
    def __init__(self, output: OutputWriter, prefix: str = "🔧"):
        self._out = output
        self._prefix = prefix
    
    def on_tool_call(self, name: str, args: dict) -> None:
        self._out.info(f"调用工具: {name}({args})")
    
    def on_tool_result(self, name: str, result: dict) -> None:
        pass  # 默认不打印结果
```

**预留说明**: 本次迭代 `Agent._execute_tool_calls` 中调用此接口的方式是 `io_backend.tool_renderer.on_tool_call(name, args)`。当前默认实现的行为等价于 print_handler。未来 Claude Code CLI 可以覆盖 `on_tool_call` 来显示 spinner + 时间，`on_tool_result` 来取消 spinner。

---

### InputReader (ABC)

```python
class InputReader(ABC):
    """输入抽象 —— 负责从来源读取用户输入。"""
    
    @abstractmethod
    def read(self, prompt: str) -> str:
        """显示 prompt 并读取用户输入。返回去除前后空白的字符串。"""
        ...
```

**约束**:
- `read` 必须显示 prompt（终端模式下等价于 `input(prompt)`）
- 发生 EOF / Ctrl+C / Ctrl+D 时返回空字符串 `""`

---

### IOBackend (dataclass)

```python
@dataclass(frozen=True)
class IOBackend:
    """统一的终端 IO 访问点：输出 + 输入 + 工具调用渲染。"""
    output: OutputWriter = MISSING        # 默认 TerminalOutputWriter
    input: InputReader = MISSING          # 默认 TerminalInputReader
    tool_renderer: ToolCallRenderer = MISSING  # 默认 DefaultToolCallRenderer
```

**为什么 frozen=True**: 一旦组装，运行时不应替换内部组件引用。偏好不同的输出目标时创建新的 IOBackend 对象。

**为什么用 MISSING 而非 field(default_factory=...)**: 让构造时可以使用默认工厂方法：

```python
@classmethod
def terminal(cls) -> IOBackend:
    """创建一个默认的终端 IO 后端。"""
    output = TerminalOutputWriter()
    return cls(
        output=output,
        input=TerminalInputReader(),
        tool_renderer=DefaultToolCallRenderer(output),
    )
```

---

## 实现类

### TerminalOutputWriter

```python
class TerminalOutputWriter(OutputWriter):
    """终端输出 —— 所有输出通过 print() 到标准输出。
    
    未来 Claude Code CLI 实现可继承此类并覆盖 info/warn/error/success
    以分流到 stderr。
    """
    def write(self, text: str) -> None:
        print(text)
```

---

### TerminalInputReader

```python
class TerminalInputReader(InputReader):
    """终端输入 —— 使用 input() 从标准输入读取。"""
    def read(self, prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""
```

---

### CaptureOutputWriter

```python
class CaptureOutputWriter(OutputWriter):
    """测试捕获 —— 将输出追加到内部列表。"""
    def __init__(self):
        self._lines: list[str] = []
    
    def write(self, text: str) -> None:
        self._lines.append(text)
    
    @property
    def lines(self) -> list[str]:
        return list(self._lines)  # 返回副本
    
    def clear(self) -> None:
        self._lines.clear()
```

---

### FixedInputReader

```python
class FixedInputReader(InputReader):
    """测试模拟 —— 按顺序返回预设回答列表。"""
    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self._index = 0
    
    def read(self, prompt: str) -> str:
        if self._index >= len(self._answers):
            raise EOFError("FixedInputReader: 预设回答列表已耗尽")
        answer = self._answers[self._index]
        self._index += 1
        return answer
    
    @property
    def remaining(self) -> list[str]:
        return self._answers[self._index:]
```

**耗尽行为**: 抛出 `EOFError`（Clarify 确认）。
