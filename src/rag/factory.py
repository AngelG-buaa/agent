"""RAG 工厂 —— 共享组件的惰性初始化与组装。

管线角色：⑦ 装配层。项目唯一 import config 来创建 RAG 组件的地方。
Indexer / Retriever 接收已构建好的共享组件，不直接读 config。

单例模式：Embedder、VectorStore、Chunker、ParserRegistry 全局共享一份。
外部消费者只需调用 get_indexer() / get_retriever()，不感知内部实例化细节。

支持多后端：通过 config.rag.store_backend 选择 "faiss"（默认）或 "qdrant"。
Retriever / Indexer 通过依赖注入接收 VectorStoreBase 子类实例，无需感知后端差异。

被依赖（外部入口）：
  tools/search_knowledge.py → get_retriever()
  tools/read_chunk.py       → get_retriever()
  tools/index_file.py       → get_indexer()
  index_cli.py              → get_indexer()
"""

import os

from config import embedding_model, rag
from rag import Embedder
from rag import FaissVectorStore
from rag.vector_store_base import VectorStoreBase
from rag import Chunker
from rag import Indexer
from rag import Retriever
from rag import ParserRegistry

# 共享组件 —— Indexer 和 Retriever 共用同一实例
_embedder = None
_store: VectorStoreBase | None = None
_chunker = None
_parser_registry = None

# 惰性单例
_indexer = None
_retriever = None


def _create_store() -> VectorStoreBase:
    """根据 config.rag.store_backend 创建对应的向量存储后端。

    Raises:
        ImportError: 选择了 qdrant 但未安装 qdrant-client。
        ValueError: 选择了 qdrant 但未配置 qdrant_url。
    """
    backend = rag.store_backend
    if backend == "qdrant":
        try:
            from rag.qdrant_store import QdrantVectorStore
        except ImportError as e:
            raise ImportError(
                "使用 Qdrant 后端需要安装 qdrant-client: pip install qdrant-client"
            ) from e
        qdrant_url = rag.qdrant_url or os.environ.get("QDRANT_URL")
        if not qdrant_url:
            raise ValueError(
                "使用 Qdrant 后端时必须配置 rag.qdrant_url 或设置环境变量 QDRANT_URL"
            )
        return QdrantVectorStore(
            url=qdrant_url,
            collection_name=rag.qdrant_collection,
            vector_size=VectorStoreBase.DIM,
        )
    else:
        # 默认后端：FAISS IndexFlatIP + JSON 元数据
        return FaissVectorStore(rag.index_dir)


def _init_shared():
    """首次调用时初始化 Embedder / VectorStore / Chunker / ParserRegistry。"""
    global _embedder, _store, _chunker, _parser_registry
    if _embedder is None:
        _embedder = Embedder(
            embedding_model.api_key,
            embedding_model.base_url,
            embedding_model.model,
        )
        _store = _create_store()
        _chunker = Chunker(
            chunk_token_size=rag.chunk_token_size,
            overlapped_percent=rag.overlapped_percent,
        )
        _parser_registry = ParserRegistry()


def get_indexer() -> Indexer:
    _init_shared()
    global _indexer
    if _indexer is None:
        _indexer = Indexer(_embedder, _chunker, _store, _parser_registry)
    return _indexer


def get_retriever() -> Retriever:
    _init_shared()
    global _retriever
    if _retriever is None:
        _retriever = Retriever(_embedder, _store, top_k=rag.top_k)
    return _retriever
