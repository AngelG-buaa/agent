"""联网搜索工具 —— 通过 DuckDuckGo 搜索互联网内容。"""

from tooling.base import Tool, ToolParameter

from ddgs import DDGS


class WebSearchTool(Tool):
    def __init__(self):
        super().__init__(
            name="web_search",
            description="联网搜索互联网内容，返回相关网页的标题、链接和摘要。",
        )
        try:
            from config import web_search as _cfg
            self._top_k = _cfg.search_top_k
        except (ImportError, AttributeError):
            self._top_k = 5

    def get_parameters(self):
        return [
            ToolParameter("query", "string", "搜索关键词或问题"),
        ]

    def run(self, params):
        with DDGS() as ddgs:
            results = list(ddgs.text(params["query"], max_results=self._top_k))
        return {"results": results}
