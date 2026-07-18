"""RAG 模块 —— 文档解析 → 分块 → 向量化 → 索引 → 检索。

══════════════════════════════════════════════════════════════════
管线阶段                    负责模块           依赖
──────────────────────────────────────────────────────────────────
① 解析   file → raw text    parser.py          (无内部依赖)
② 分块   raw text → chunks  chunker.py         (无内部依赖)
③ 向量化 chunks → vectors   embedder.py        (无内部依赖)
④ 存储   vectors + meta     vector_store.py    → chunk_info.py
⑤ 索引   ①-④ 编排           indexer.py         注入 embedder/chunker/store/parser
⑥ 检索   query → results    retriever.py       → chunk_info.py
⑦ 装配   组件创建与注入      factory.py         → 上述所有模块
⑧ 数据   chunk 类型定义      chunk_info.py      (无内部依赖)
⑨ 提示词 引用规范模板        prompts.py         (无内部依赖)
══════════════════════════════════════════════════════════════════

外部消费者：
  tools/search_knowledge.py → rag.factory.get_retriever()
  tools/read_chunk.py       → rag.factory.get_retriever() + rag.prompts
  tools/index_file.py       → rag.factory.get_indexer()
  index_cli.py              → rag.factory.get_indexer()

导入建议：
  业务代码请通过 rag.factory 获取共享组件，不要直接实例化内部类。
  类型标注可从此包直接导入，如：from rag import ChunkInfo, VectorStoreBase

后端支持：
  FAISS  (默认) — IndexFlatIP + JSON 元数据持久化，零外部依赖
  Qdrant         — 独立向量数据库，支持持久化与分布式部署
"""

from rag.chunk_info import ChunkInfo
from rag.chunker import Chunker
from embedding import Embedder
from rag.vector_store_base import VectorStoreBase
from rag.faiss_store import FaissVectorStore, VectorStore  # VectorStore = 向后兼容别名
from rag.indexer import Indexer
from rag.retriever import Retriever
from rag.parser import BaseParser, TextParser, PdfParser, ParserRegistry
