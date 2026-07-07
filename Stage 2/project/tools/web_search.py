"""联网搜索工具 —— 通过 DuckDuckGo 搜索互联网内容。"""

from tool import Tool, ToolParameter

# ddgs 是可选依赖 —— 仅在调用搜索时需要。
# 模块顶层使用 try/except 导入，使得本文件始终可被 import，
# 只在缺少依赖且实际执行搜索时才抛出 ImportError。
try:
    from ddgs import DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    _DDGS_AVAILABLE = False


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
        # if not _DDGS_AVAILABLE:
        #     raise ImportError(
        #         "使用联网搜索需要安装 ddgs: pip install ddgs"
        #     )
        with DDGS() as ddgs:
            results = list(ddgs.text(params["query"], max_results=self._top_k))
        return {"results": results}
