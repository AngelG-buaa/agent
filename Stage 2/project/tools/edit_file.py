"""文件编辑工具 —— 在工作区文件内做精确文本替换（首次出现处）。"""

from pathlib import Path

from tooling.base import Tool, ToolParameter, RiskLevel


class EditFileTool(Tool):
    """文件编辑工具 —— risk_level=SENSITIVE。

    路径安全边界由 PermissionEngine（PolicySettingsSource）负责。
    本工具只做路径解析，不做安全判断。
    """

    def __init__(self, base_dir: str | Path | None = None):
        super().__init__(
            name="edit_file",
            description="编辑文件：查找 old_text 首次出现并替换为 new_text。路径相对于工作区根目录。",
            risk_level=RiskLevel.SENSITIVE,
        )
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()

    def get_parameters(self):
        return [
            ToolParameter("path", "string", "要编辑的文件路径，相对于工作区根目录"),
            ToolParameter("old_text", "string", "要被替换的原文本（精确匹配）"),
            ToolParameter("new_text", "string", "替换后的新文本"),
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
        old_text = parameters["old_text"]
        new_text = parameters["new_text"]

        file_path = Tool.resolve_path(path_str, self._base_dir)

        if not file_path.exists():
            return {"error": f"文件不存在: {path_str}"}

        original = file_path.read_text(encoding="utf-8")

        if old_text not in original:
            return {"replaced": False, "error": "old_text 在文件中未找到"}

        replaced = original.replace(old_text, new_text, 1)
        file_path.write_text(replaced, encoding="utf-8")

        return {
            "path": str(file_path),
            "replaced": True,
            "lines_changed": abs(replaced.count("\n") - original.count("\n")),
        }
