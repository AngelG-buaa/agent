"""网页抓取工具 —— 通过 Jina Reader API 将网页转为 Markdown。"""

import requests

from tool import Tool, ToolParameter


class WebFetchTool(Tool):
    def __init__(self):
        super().__init__(
            name="web_fetch",
            description="抓取网页内容并转为 Markdown。传入一个或多个 URL，返回对应的正文。",
        )
        try:
            from config import web_search as _cfg
            self._jina_reader_base = _cfg.jina_reader_base.rstrip("/")
        except (ImportError, AttributeError):
            self._jina_reader_base = "https://r.jina.ai"

    def get_parameters(self):
        return [
            ToolParameter("urls", "array",
                          "要抓取的网页 URL 列表，如 ['https://example.com', 'https://other.com']。",
                          required=True),
        ]

    def run(self, params):
        urls = params["urls"]
        if not isinstance(urls, list):
            urls = [urls]
        pages = {}
        for url in urls:
            # 逐 URL 独立 try/except：单个失败不影响其他，错误信息保留在 value 中
            try:
                resp = requests.get(
                    f"{self._jina_reader_base}/{url}",
                    headers={
                        "Accept": "text/markdown",
                        "User-Agent": "AI4ML-Agent/1.0",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                pages[url] = resp.text
            except Exception as exc:
                pages[url] = f"抓取失败: {exc}"
        return {"pages": pages}
