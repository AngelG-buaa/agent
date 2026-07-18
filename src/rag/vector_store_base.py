"""向量存储抽象基类 —— 定义向量存储的统一接口。

管线角色：④ 存储层接口。所有向量存储实现必须实现此 ABC。
依赖：chunk_info（ChunkInfo）。

抽象方法：
  add()       — 添加一批 chunk 向量和元数据
  search()    — 余弦相似度检索
  get_by_id() — 按 chunk ID 获取完整元数据
  count       — 当前索引总数（property）

具体实现：
  FaissVectorStore  — FAISS IndexFlatIP + JSON 元数据（vector_store.py）
  QdrantVectorStore — Qdrant 向量数据库（qdrant_store.py）
"""

from abc import ABC, abstractmethod

import numpy as np

from rag.chunk_info import ChunkInfo


class VectorStoreBase(ABC):
    """向量存储的抽象基类。

    定义所有向量存储实现必须遵守的接口契约。
    Indexer 和 Retriever 通过此接口进行依赖注入，不感知具体后端。
    """

    DIM: int = 1024  # BGE-M3 输出维度

    @abstractmethod
    def add(self, vectors: np.ndarray, texts: list[str], file_path: str,
            title: str | None = None, doc_metadata: dict | None = None) -> int:
        """添加一批 chunk。返回当前索引总数。

        Args:
            vectors: shape (N, D) 的 float32 向量矩阵（D = 嵌入维度）。
            texts: 每个 chunk 的原始文本。
            file_path: 来源文件路径。
            title: 文档标题，None 时由上层降级处理。
            doc_metadata: 可选元数据。
        Returns:
            添加后存储中的 chunk 总数。
        """
        ...

    @abstractmethod
    def search(self, query_vec: np.ndarray, top_k: int = 5) -> list[ChunkInfo]:
        """余弦相似度检索，返回 top_k 个结果。

        Args:
            query_vec: shape (D,) 的查询向量。
            top_k: 返回的最大结果数。
        Returns:
            按相似度降序排列的 ChunkInfo 列表，每项含 score 字段。
        """
        ...

    @abstractmethod
    def get_by_id(self, chunk_id: int) -> ChunkInfo | None:
        """按 chunk ID 获取完整元数据 + 正文。

        Args:
            chunk_id: chunk 的唯一标识符。
        Returns:
            对应的 ChunkInfo，ID 不存在时返回 None。
        """
        ...

    @property
    @abstractmethod
    def count(self) -> int:
        """当前存储中的 chunk 总数。"""
        ...
