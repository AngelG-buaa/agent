"""文件写入工具 —— 在工作区内创建或覆盖文件。"""

from pathlib import Path

from tooling.base import Tool, ToolParameter, RiskLevel


class WriteFileTool(Tool):
    """文件写入工具 —— risk_level=SENSITIVE。

    路径安全边界由 PermissionEngine（PolicySettingsSource）负责。
    本工具只做路径解析，不做安全判断。
    """

    def __init__(self, base_dir: str | Path | None = None):
        super().__init__(
            name="write_file",
            description="在工作区内创建或覆盖文件。路径相对于工作区根目录。",
            risk_level=RiskLevel.SENSITIVE,
        )
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()

    def get_parameters(self):
        return [
            ToolParameter("path", "string", "文件路径，相对于工作区根目录"),
            ToolParameter("content", "string", "要写入的文件内容"),
        ]

    # ---- 权限管线 ----

    def permission_target(self, params: dict) -> str:
        """rule_content 匹配目标：原始路径。

        不在此处 resolve —— deny/ask 规则通过 condition 函数自行判断，
        fnmatch 模式匹配需要原始路径才能对上 ".git/*" 等相对 pattern。
        """
        return params.get("path", "")

    # ---- 执行 ----

    def run(self, parameters: dict) -> dict:
        path_str = parameters["path"]
        content = parameters["content"]

        file_path = Tool.resolve_path(path_str, self._base_dir)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

        return {
            "path": str(file_path),
            "bytes_written": len(content.encode("utf-8")),
        }
