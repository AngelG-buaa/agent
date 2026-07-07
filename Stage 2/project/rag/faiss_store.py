"""向量存储 —— FAISS IndexFlatIP + JSON 元数据持久化。

管线角色：④ 存储层。接收向量和 ChunkInfo，持久化到 FAISS + metadata.json。
依赖：chunk_info（ChunkInfo）。被 indexer（写入）和 retriever（读取）共用。

文件结构：
  data/rag_index/
    index.faiss     — FAISS 原生二进制索引
    metadata.json   — [{id, text, file_path, title, doc_metadata}, ...]
"""

import json
import os
from dataclasses import replace

import numpy as np
import faiss

from rag import ChunkInfo
from rag.vector_store_base import VectorStoreBase


class FaissVectorStore(VectorStoreBase):
    """管理 chunk 向量索引和元数据的持久化存储（基于 FAISS IndexFlatIP）。

    文件结构：
      data/rag_index/
        index.faiss     — FAISS 原生二进制索引
        metadata.json   — [{id, text, file_path, title, doc_metadata}, ...]
    """

    def __init__(self, index_dir: str):
        self.index_dir = index_dir
        os.makedirs(index_dir, exist_ok=True)
        self._index_path = os.path.join(index_dir, "index.faiss")
        self._meta_path = os.path.join(index_dir, "metadata.json")
        self._index: faiss.IndexFlatIP | None = None
        self._metadata: list[ChunkInfo] = []
        self._load()

    # ── public ────────────────────────────────────────────────

    def add(self, vectors: np.ndarray, texts: list[str], file_path: str,
            title: str | None = None, doc_metadata: dict | None = None) -> int:
        """添加一批 chunk。返回当前索引总数。"""
        if len(vectors.shape) == 1:
            vectors = vectors.reshape(1, -1)
        vectors = vectors.astype(np.float32)

        # L2 归一化使内积等价余弦相似度
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        vectors = vectors / norms

        self._index.add(vectors)

        start_id = self._index.ntotal - len(texts)
        for i, text in enumerate(texts):
            self._metadata.append(ChunkInfo(
                id=start_id + i,
                text=text,
                file_path=file_path,
                title=title,
                chunk_index=i,
                doc_metadata=doc_metadata,
            ))
        self._save()
        return self._index.ntotal

    def search(self, query_vec: np.ndarray, top_k: int = 5) -> list[ChunkInfo]:
        """余弦相似度检索，返回 top_k 个结果。"""
        query_vec = query_vec.reshape(1, -1).astype(np.float32)
        norm = np.linalg.norm(query_vec)
        if norm > 0.0:
            query_vec = query_vec / norm
        distances, indices = self._index.search(query_vec, min(top_k, self._index.ntotal))

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            meta = replace(self._metadata[idx], score=round(float(dist), 4))
            results.append(meta)
        return results

    def get_by_id(self, chunk_id: int) -> ChunkInfo | None:
        """按 chunk ID 获取完整元数据 + 正文。O(1) 直接索引。"""
        if 0 <= chunk_id < len(self._metadata):
            return self._metadata[chunk_id]
        return None

    @property
    def count(self) -> int:
        return self._index.ntotal

    # ── internal ──────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._index_path):
            self._index = faiss.read_index(self._index_path)
        else:
            self._index = faiss.IndexFlatIP(self.DIM)

        if os.path.exists(self._meta_path):
            with open(self._meta_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._metadata = [ChunkInfo.from_dict(d) for d in raw]

    def _save(self):
        faiss.write_index(self._index, self._index_path)
        data = [c.to_store_dict() for c in self._metadata]
        with open(self._meta_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(self._meta_path + ".tmp", self._meta_path)  # 原子替换


# 向后兼容别名 —— 旧代码仍可 `from rag import VectorStore`
VectorStore = FaissVectorStore
