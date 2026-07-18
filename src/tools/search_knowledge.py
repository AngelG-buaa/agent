"""知识检索工具 —— 返回元数据（ID/标题/摘要），不含完整正文。"""

from tooling.base import Tool, ToolParameter
from rag.factory import get_retriever


class SearchKnowledgeTool(Tool):
    def __init__(self):
        super().__init__(
            name="search_knowledge",
            description="在本地知识库中搜索。返回 ID、标题、摘要",
        )

    def get_parameters(self):
        return [
            ToolParameter("query", "string", "检索查询，建议使用自然语言问题"),
        ]

    def run(self, params):
        result = get_retriever().search(params["query"])
        return {"results": result["results"]}
