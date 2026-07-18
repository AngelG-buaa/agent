"""文件读取工具 —— 读取工作区内的文件内容。"""

from pathlib import Path

from tooling.base import Tool, ToolParameter


class ReadFileTool(Tool):

    def __init__(self, base_dir: str | Path | None = None):
        super().__init__(
            name="read_file",
            description="读取工作区内的文件内容。支持指定行范围读取大文件。",
        )
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()

    def get_parameters(self):
        return [
            ToolParameter("path", "string", "文件路径，相对于工作区根目录"),
            ToolParameter("offset", "integer", "起始行号（1-based，默认 1）", required=False),
            ToolParameter("limit", "integer", "读取行数（默认全部）", required=False),
        ]

    def run(self, parameters: dict) -> dict:
        path_str = parameters["path"]
        offset = parameters.get("offset", 1) - 1    # 1-based → 0-based
        limit = parameters.get("limit")

        file_path = (self._base_dir / path_str).resolve()

        if not file_path.is_file():
            return {"error": f"文件不存在: {path_str}"}

        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            # Fallback: try GBK (common on Chinese Windows)
            try:
                lines = file_path.read_text(encoding="gbk").splitlines()
            except Exception as exc:
                return {"error": f"文件编码无法识别: {exc}"}
        except Exception as exc:
            return {"error": f"读取文件失败: {exc}"}

        total_lines = len(lines)

        if offset < 0 or offset >= total_lines:
            return {"error": f"起始行 {offset + 1} 超出范围 (总行数: {total_lines})"}

        end = offset + limit if limit else total_lines
        selected = lines[offset:end]

        return {
            "path": str(file_path),
            "content": "\n".join(selected),
            "lines_read": len(selected),
            "total_lines": total_lines,
        }
