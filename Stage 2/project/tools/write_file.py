"""文件写入工具 —— 在项目工作区内创建或覆盖文件。"""

from pathlib import Path

from config import WORKDIR
from tool import Tool, ToolParameter


class WriteFileTool(Tool):
    def __init__(self):
        super().__init__(
            name="write_file",
            description="在项目工作区内创建或覆盖文件。路径相对于项目根目录。",
            risk_level="sensitive",
        )

    def get_parameters(self):
        return [
            ToolParameter("path", "string", "文件路径，相对于项目根目录"),
            ToolParameter("content", "string", "要写入的文件内容"),
        ]

    def run(self, parameters: dict) -> dict:
        path_str = parameters["path"]
        content = parameters["content"]

        # 路径安全解析
        file_path = self._resolve_safe_path(path_str)

        # 确保父目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入（UTF-8）
        file_path.write_text(content, encoding="utf-8")

        return {
            "path": str(file_path),
            "bytes_written": len(content.encode("utf-8")),
        }

    @staticmethod
    def _resolve_safe_path(path_str: str) -> Path:
        """安全解析：相对路径基于 WORKDIR，不允许 .. 穿梭到 WORKDIR 之外。

        注意：此方法仅做路径解析校验，实际的安全边界判断由 PermissionEngine 完成。
        """
        workdir = Path(WORKDIR).resolve()
        candidate = (workdir / path_str).resolve()

        if not candidate.is_relative_to(workdir):
            raise ValueError(f"路径超出工作区范围: {path_str} → {candidate}")

        return candidate
