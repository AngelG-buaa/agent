"""Shell 命令执行工具 —— 在项目工作区内执行 bash 命令。

⚠️ 安全模型说明（Best-Effort Denylist）:
  本工具使用 subprocess.run(shell=True)，本质上是向 LLM 授予完整的 shell 访问权限。
  权限层通过字符串模式匹配来拦截危险操作，但这不是真正的沙箱 ——

  - 同一危险操作有无数种写法，字符串匹配无法穷举
  - Python/Node/Perl/Ruby/PowerShell 等解释器的 -c/-e 调用已被拦截
  - 但不排除存在其他绕过方式（编译型语言、编码混淆、环境变量注入等）

  这是工程权衡：完整沙箱（Docker/gVisor）会增加部署复杂度。
  当前设计适用于受信任的 LLM 在隔离开发环境中使用。
  生产环境或不可信 LLM 场景中，请将 bash 替换为受沙箱约束的执行后端。
"""

import subprocess
from pathlib import Path

from tooling.base import Tool, ToolParameter


class BashTool(Tool):

    def __init__(self, workdir: str | Path | None = None):
        super().__init__(
            name="bash",
            description="在项目工作区内执行 shell 命令。支持文件操作、程序运行等。",
        )
        self._workdir = Path(workdir) if workdir else Path.cwd()

    def get_parameters(self):
        return [
            ToolParameter("command", "string", "要执行的 shell 命令"),
            ToolParameter("timeout", "integer", "命令超时秒数，默认 120", required=False),
        ]

    # ---- 执行 ----

    def run(self, parameters: dict) -> dict:
        command = parameters["command"]
        timeout = parameters.get("timeout", 120)

        result = subprocess.run(
            command,
            shell=True,
            cwd=str(self._workdir),
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
