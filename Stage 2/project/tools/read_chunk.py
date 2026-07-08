"""按需读取 chunk 正文工具。"""

from tooling.base import Tool, ToolParameter
from rag.factory import get_retriever
from rag.prompts import CITATION_GUIDELINES


class ReadChunkTool(Tool):
    def __init__(self):
        super().__init__(
            name="read_chunk",
            description="读取指定 chunk ID 的完整正文。可一次读取多个",
        )

    def get_parameters(self):
        return [
            ToolParameter("chunk_ids", "array",
                          "要读取的 chunk ID 列表，如 [0, 3, 7]。每个 ID 必须是整数。",
                          required=True),
        ]

    def run(self, params):
        chunk_ids = params["chunk_ids"]
        if not isinstance(chunk_ids, list):
            chunk_ids = [chunk_ids]
        result = get_retriever().get_chunks(chunk_ids)
        # 引用规范随正文一起返回，关联性更强，且不残留在后续对话轮次中
        result["content"] += f"\n\n{CITATION_GUIDELINES}"
        return result
