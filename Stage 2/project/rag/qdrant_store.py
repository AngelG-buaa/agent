"""向量存储 —— Qdrant 向量数据库 + Cosine 距离。

管线角色：④ 存储层。接收向量和 ChunkInfo，持久化到 Qdrant 集合。
依赖：chunk_info（ChunkInfo）。被 indexer（写入）和 retriever（读取）共用。

与 FAISS FaissVectorStore 接口完全一致（add / search / get_by_id / count / DIM），
可无缝替换。区别在于数据驻留在 Qdrant 服务端而非本地磁盘文件。

Qdrant 集合：
  集合名:   可配置（默认 rag_documents）
  向量维度: 1024（BGE-M3）
  距离度量: Cosine
  Point ID: 自增整数，与 ChunkInfo.id 严格一致
  Payload:  {text, file_path, title, doc_metadata}

环境变量：
  QDRANT_URL  — Qdrant REST 地址（默认 http://localhost:6333），
                 仅在 config.rag.qdrant_url 为 None 时作为后备。
"""

import numpy as np

from rag import ChunkInfo
from rag.vector_store_base import VectorStoreBase

# qdrant-client 是可选依赖 —— 仅在实例化 QdrantVectorStore 时需要。
# 模块顶层使用 try/except 导入，使得本文件始终可被 import（用于类型检查等），
# 只在缺少依赖且实际调用构造函数时才抛出 ImportError。
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False


class QdrantVectorStore(VectorStoreBase):
    """管理 chunk 向量索引和元数据的 Qdrant 持久化存储。

    构造时不校验服务连通性；实际读写操作在服务不可用时抛出
    qdrant_client 原生异常，由上层调用方处理。
    """

    def __init__(self, url: str, collection_name: str = "rag_documents",
                 vector_size: int = 1024):
        """初始化 Qdrant 客户端并确保集合存在。

        Args:
            url: Qdrant REST 地址（如 http://localhost:6333）。
            collection_name: Qdrant 集合名称。
            vector_size: 向量维度，默认 1024（BGE-M3）。
        """
        if not _QDRANT_AVAILABLE:
            raise ImportError(
                "使用 Qdrant 后端需要安装 qdrant-client: pip install qdrant-client"
            )
        self._client = QdrantClient(url=url)
        self._collection_name = collection_name
        self._vector_size = vector_size
        self._next_id: int = 0

        self._ensure_collection()

        try:
            self._next_id = self._client.count(
                collection_name=self._collection_name
            ).count
        except Exception:
            self._next_id = 0

    # ── public ────────────────────────────────────────────────

    def add(self, vectors: np.ndarray, texts: list[str], file_path: str,
            title: str | None = None, doc_metadata: dict | None = None) -> int:
        """添加一批 chunk。返回当前索引总数。

        向量经 L2 归一化后写入 Qdrant，使 Cosine 度量等价余弦相似度。
        Point ID 使用自增整数序列，与 ChunkInfo.id 一一对应。
        """
        if len(vectors.shape) == 1:
            vectors = vectors.reshape(1, -1)
        vectors = vectors.astype(np.float32)

        # L2 归一化 —— 归一化后 Cosine 距离等价余弦相似度
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        vectors = vectors / norms

        batch_size = len(texts)
        points: list[PointStruct] = []
        for i, text in enumerate(texts):
            pid = self._next_id + i
            points.append(PointStruct(
                id=pid,
                vector=vectors[i].tolist(),
                payload={
                    "text": text,
                    "file_path": file_path,
                    "title": title or "",
                    "doc_metadata": doc_metadata or {},
                },
            ))

        self._client.upsert(
            collection_name=self._collection_name,
            points=points,
            wait=True,
        )
        self._next_id += batch_size
        return self._next_id

    def search(self, query_vec: np.ndarray, top_k: int = 5) -> list[ChunkInfo]:
        """余弦相似度检索，返回 top_k 个 ChunkInfo（含 score 字段）。

        score 为余弦相似度，取值范围 [-1, 1]，值越大越相似。
        行为与 FAISS IndexFlatIP + L2-normalize 等价。
        """
        query_vec = query_vec.reshape(1, -1).astype(np.float32)
        norm = np.linalg.norm(query_vec)
        if norm > 0.0:
            query_vec = query_vec / norm

        limit = min(top_k, max(self.count, 1))
        results = self._client.search(
            collection_name=self._collection_name,
            query_vector=query_vec[0].tolist(),
            limit=limit,
            with_payload=True,
        )

        output: list[ChunkInfo] = []
        for point in results:
            payload = point.payload or {}
            s = point.score
            output.append(ChunkInfo(
                id=point.id,
                text=payload.get("text", ""),
                file_path=payload.get("file_path", ""),
                title=payload.get("title") or None,
                doc_metadata=payload.get("doc_metadata") or None,
                score=round(float(s), 4) if s is not None else None,
            ))
        return output

    def get_by_id(self, chunk_id: int) -> ChunkInfo | None:
        """按 chunk ID 获取完整元数据 + 正文。

        直接查询 Qdrant 服务端，不依赖本地缓存。
        不存在的 ID 返回 None。
        """
        results = self._client.retrieve(
            collection_name=self._collection_name,
            ids=[chunk_id],
            with_payload=True,
        )
        if not results:
            return None
        point = results[0]
        payload = point.payload or {}
        return ChunkInfo(
            id=point.id,
            text=payload.get("text", ""),
            file_path=payload.get("file_path", ""),
            title=payload.get("title") or None,
            doc_metadata=payload.get("doc_metadata") or None,
        )

    @property
    def count(self) -> int:
        """集合中的 point 总数。服务不可用时返回 0。"""
        try:
            return self._client.count(
                collection_name=self._collection_name
            ).count
        except Exception:
            return 0

    # ── internal ──────────────────────────────────────────────

    def _ensure_collection(self) -> None:
        """确保目标集合存在；不存在则创建（Cosine 距离）。"""
        collections = self._client.get_collections().collections
        names = {c.name for c in collections}
        if self._collection_name not in names:
            self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(
                    size=self._vector_size,
                    distance=Distance.COSINE,
                ),
            )
