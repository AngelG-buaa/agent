"""文件编辑工具 —— 在项目文件内做精确文本替换（首次出现处）。"""

from pathlib import Path

from config import WORKDIR
from tool import Tool, ToolParameter


class EditFileTool(Tool):
    def __init__(self):
        super().__init__(
            name="edit_file",
            description="编辑项目文件：查找 old_text 首次出现的位置并替换为 new_text。路径相对于项目根目录。",
            risk_level="sensitive",
        )

    def get_parameters(self):
        return [
            ToolParameter("path", "string", "要编辑的文件路径，相对于项目根目录"),
            ToolParameter("old_text", "string", "要被替换的原文本（精确匹配）"),
            ToolParameter("new_text", "string", "替换后的新文本"),
        ]

    def run(self, parameters: dict) -> dict:
        path_str = parameters["path"]
        old_text = parameters["old_text"]
        new_text = parameters["new_text"]

        # 路径安全解析
        file_path = self._resolve_safe_path(path_str)

        if not file_path.exists():
            return {"error": f"文件不存在: {path_str}"}

        original = file_path.read_text(encoding="utf-8")

        if old_text not in original:
            return {"replaced": False, "error": "old_text 在文件中未找到"}

        # 只替换首次出现
        replaced = original.replace(old_text, new_text, 1)
        file_path.write_text(replaced, encoding="utf-8")

        return {
            "path": str(file_path),
            "replaced": True,
            "lines_changed": abs(replaced.count("\n") - original.count("\n")),
        }

    @staticmethod
    def _resolve_safe_path(path_str: str) -> Path:
        workdir = Path(WORKDIR).resolve()
        candidate = (workdir / path_str).resolve()

        if not candidate.is_relative_to(workdir):
            raise ValueError(f"路径超出工作区范围: {path_str} → {candidate}")

        return candidate
