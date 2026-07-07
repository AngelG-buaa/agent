"""知识检索器 —— 编码查询 → 向量搜索 → 按 ID 获取正文。

管线角色：⑥ 检索层。接收外部 query，通过 embedder 向量化后搜索 VectorStore。
依赖：chunk_info（ChunkInfo.to_context 格式化检索结果）。
      embedder 和 store 通过构造函数注入（由 factory.py 装配）。

两个核心方法：
  search()     — "翻图书索引卡"，返回元数据 + 摘要（search_knowledge 工具用）
  get_chunks() — "翻开书读正文"，返回 Markdown 格式全文（read_chunk 工具用）
"""

from rag.chunk_info import ChunkInfo


class Retriever:
    """负责从已有索引中检索知识。依赖注入 Embedder / VectorStore。"""

    def __init__(self, embedder, store, top_k=5):
        self._embedder = embedder
        self._store = store
        self._top_k = top_k

    def search(self, query: str, top_k: int | None = None) -> dict:
        """搜索知识库，只返回元数据（ID、标题、摘要、分数），不含完整正文。

        对标"翻图书索引卡"——Agent 拿到摘要后自行决定读哪些 chunk。
        """
        if top_k is None:
            top_k = self._top_k

        query_vec = self._embedder.encode_query(query)
        raw_results = self._store.search(query_vec, top_k=top_k)
        return {"results": [r.to_search_result() for r in raw_results]}

    def get_chunks(self, chunk_ids: list[int]) -> dict:
        """按 ID 列表获取完整 chunk 正文。

        对标"翻开书读正文"——Agent 在 search 后按需调用。
        不存在的 ID 会在 content 末尾以 Markdown 斜体提示。
        """
        chunks = []
        missing = []
        for cid in chunk_ids:
            chunk = self._store.get_by_id(cid)
            if chunk:
                chunks.append(chunk)
            else:
                missing.append(cid)
        content = ChunkInfo.to_context(chunks)
        if missing:
            content += f"\n\n_以下 ID 不存在：{missing}_"
        return {"content": content}
