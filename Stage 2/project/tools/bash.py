"""Shell 命令执行工具 —— 在项目工作区内执行 bash 命令。"""

import subprocess

from config import WORKDIR
from tool import Tool, ToolParameter


class BashTool(Tool):
    def __init__(self):
        super().__init__(
            name="bash",
            description="在项目工作区内执行 shell 命令。支持文件操作、程序运行等。",
            risk_level="destructive",
        )

    def get_parameters(self):
        return [
            ToolParameter("command", "string", "要执行的 shell 命令"),
            ToolParameter(
                "timeout",
                "integer",
                "命令超时秒数，默认 120",
                required=False,
            ),
        ]

    def run(self, parameters: dict) -> dict:
        command = parameters["command"]
        timeout = parameters.get("timeout", 120)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(WORKDIR),
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"命令执行超时（{timeout}s）: {command}"}
        except Exception as exc:
            return {"error": str(exc)}
