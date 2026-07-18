"""终端 IO 抽象层 —— OutputWriter / InputReader / ToolCallRenderer / IOBackend。

本模块定义统一的终端 IO 接口，将项目中散落的 print()/input() 调用
抽象为可替换的 IOBackend，通过构造参数注入到 Agent、TodoWriteTool、
TerminalApprover 等消费者中。

设计目标:
  - 测试可验证输出：CaptureOutputWriter + FixedInputReader
  - 语义可区分：info/warn/error/success 为未来 UI 预留挂钩
  - 工具调用渲染独立：ToolCallRenderer 承载结构化事件通知
  - 未来可替换：IOBackend 三个插座（output / input / tool_renderer）
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════
# OutputWriter — 输出抽象
# ═══════════════════════════════════════════════════════════════


class OutputWriter(abc.ABC):
    """输出抽象 —— 将文本输出到目标（终端、捕获列表、文件等）。

    所有语义方法（info/warn/error/success）默认委托给 write()，
    子类按需覆盖以实现不同渲染（stderr 分流、颜色染色等）。
    """

    @abc.abstractmethod
    def write(self, text: str) -> None:
        """输出一段文本。所有语义方法的默认下落点。"""
        ...

    # ── 语义方法（默认 = write，子类按需覆盖）──

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


# ═══════════════════════════════════════════════════════════════
# InputReader — 输入抽象
# ═══════════════════════════════════════════════════════════════


class InputReader(abc.ABC):
    """输入抽象 —— 从来源读取用户输入。"""

    @abc.abstractmethod
    def read(self, prompt: str) -> str:
        """显示 prompt 并读取用户输入。返回去除前后空白的字符串。"""
        ...


# ═══════════════════════════════════════════════════════════════
# ToolCallRenderer — 工具调用渲染抽象
# ═══════════════════════════════════════════════════════════════


class ToolCallRenderer(abc.ABC):
    """工具调用渲染回调 —— 结构化地接收工具生命周期事件。

    这不等同于普通文本输出 —— 它是 Agent 运行过程中的事件通知，
    有明确的开始/结束生命期。未来 UI 可以在此实现:
    - on_tool_call: 启动 spinner + 计时
    - on_tool_result: 取消 spinner，显示结果摘要
    """

    @abc.abstractmethod
    def on_tool_call(self, name: str, args: dict) -> None:
        """工具即将被调用。name=工具名, args=工具参数。"""
        ...

    def on_tool_result(self, name: str, result: dict) -> None:
        """工具执行完成。默认不做任何事。"""
        ...


# ═══════════════════════════════════════════════════════════════
# 默认终端实现
# ═══════════════════════════════════════════════════════════════


class TerminalOutputWriter(OutputWriter):
    """终端输出 —— 使用 print() 输出到标准输出。"""

    def write(self, text: str) -> None:
        print(text, flush=True)


class TerminalInputReader(InputReader):
    """终端输入 —— 使用 input() 从标准输入读取。"""

    def read(self, prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""


class DefaultToolCallRenderer(ToolCallRenderer):
    """默认终端渲染 —— 等价于当前 print_handler 的行为。"""

    def __init__(self, output: OutputWriter | None = None):
        self._out = output or TerminalOutputWriter()

    def on_tool_call(self, name: str, args: dict) -> None:
        self._out.info(f"调用工具: {name}({args})")


# ═══════════════════════════════════════════════════════════════
# 测试实现
# ═══════════════════════════════════════════════════════════════


class CaptureOutputWriter(OutputWriter):
    """测试捕获 —— 将输出追加到内部可断言的列表。"""

    def __init__(self):
        self._lines: list[str] = []

    def write(self, text: str) -> None:
        self._lines.append(text)

    @property
    def lines(self) -> list[str]:
        """返回当前已捕获行的副本。"""
        return list(self._lines)

    def clear(self) -> None:
        """清空已捕获内容（幂等）。"""
        self._lines.clear()


class FixedInputReader(InputReader):
    """测试模拟 —— 按顺序返回预设回答列表。耗尽时抛出 EOFError。"""

    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self._index = 0

    def read(self, prompt: str) -> str:
        if self._index >= len(self._answers):
            raise EOFError("FixedInputReader: preset answers exhausted")
        answer = self._answers[self._index]
        self._index += 1
        return answer

    @property
    def remaining(self) -> list[str]:
        """尚未消耗的预设回答。"""
        return self._answers[self._index:]


# ═══════════════════════════════════════════════════════════════
# IOBackend — 统一容器
# ═══════════════════════════════════════════════════════════════


def _default_renderer() -> ToolCallRenderer:
    """默认 ToolCallRenderer 工厂。"""
    return DefaultToolCallRenderer()


@dataclass(frozen=True)
class IOBackend:
    """统一的终端 IO 访问点。

    三个字段对应工作流的三个插座:
      output        — 系统对用户的输出（回答、状态、错误）
      input         — 用户对系统的输入（授权、命令）
      tool_renderer — 工具调用过程渲染（事件型，非文本）

    一旦组装，运行时不应替换内部引用。偏好不同的输出目标时
    创建新的 IOBackend 对象。
    """

    output: OutputWriter = field(default_factory=TerminalOutputWriter)
    input: InputReader = field(default_factory=TerminalInputReader)
    tool_renderer: ToolCallRenderer = field(default_factory=_default_renderer)

    @classmethod
    def terminal(cls) -> "IOBackend":
        """创建一个标准的终端 IO 后端。"""
        out = TerminalOutputWriter()
        return cls(
            output=out,
            input=TerminalInputReader(),
            tool_renderer=DefaultToolCallRenderer(out),
        )
