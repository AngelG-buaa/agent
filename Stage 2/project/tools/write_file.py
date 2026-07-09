"""文件写入工具 —— 在工作区内创建或覆盖文件。"""

from pathlib import Path

from tooling.base import Tool, ToolParameter


class WriteFileTool(Tool):
    """文件写入工具。路径安全边界由权限引擎负责，工具只做路径解析。"""

    def __init__(self, base_dir: str | Path | None = None):
        super().__init__(
            name="write_file",
            description="在工作区内创建或覆盖文件。路径相对于工作区根目录。",
        )
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()

    def get_parameters(self):
        return [
            ToolParameter("path", "string", "文件路径，相对于工作区根目录"),
            ToolParameter("content", "string", "要写入的文件内容"),
        ]

    # ---- 执行 ----

    def run(self, parameters: dict) -> dict:
        path_str = parameters["path"]
        content = parameters["content"]

        file_path = (self._base_dir / path_str).resolve()

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

        return {
            "path": str(file_path),
            "bytes_written": len(content.encode("utf-8")),
        }
