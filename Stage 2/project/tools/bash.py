"""Shell 命令执行工具 —— 在项目工作区内执行 bash 命令。"""

import subprocess
from pathlib import Path

from tooling.base import Tool, ToolParameter, RiskLevel


class BashTool(Tool):
    """Bash 工具 —— risk_level=DESTRUCTIVE。

    安全策略由 PolicySettingsSource 统一管理，不在此处自检。
    """

    def __init__(self, workdir: str | Path | None = None):
        super().__init__(
            name="bash",
            description="在项目工作区内执行 shell 命令。支持文件操作、程序运行等。",
            risk_level=RiskLevel.DESTRUCTIVE,
        )
        self._workdir = Path(workdir) if workdir else Path.cwd()

    def get_parameters(self):
        return [
            ToolParameter("command", "string", "要执行的 shell 命令"),
            ToolParameter("timeout", "integer", "命令超时秒数，默认 120", required=False),
        ]

    # ---- 权限管线 ----

    def permission_target(self, params: dict) -> str:
        """rule_content 匹配目标：bash 命令字符串。"""
        return params.get("command", "")

    # ---- 执行 ----

    def run(self, parameters: dict) -> dict:
        command = parameters["command"]
        timeout = parameters.get("timeout", 120)

        try:
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
        except subprocess.TimeoutExpired:
            return {"error": f"命令执行超时（{timeout}s）: {command}"}
        except Exception as exc:
            return {"error": str(exc)}
