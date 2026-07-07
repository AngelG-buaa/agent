"""文档索引器 —— 解析文件 → 分块 → 向量化 → 写入 VectorStore。

管线角色：⑤ 编排层。串联 parser → chunker → embedder → store 四个阶段。
依赖：所有依赖通过构造函数注入（由 factory.py 装配），不直接 import 内部模块。

外部入口：通过 rag.factory.get_indexer() 获取单例。
  - index_cli.py（离线命令行摄入）
  - tools/index_file.py（Agent 工具，未注册）
"""

from pathlib import Path


class Indexer:
    """负责将文档摄入 RAG 系统。依赖注入 ParserRegistry / Embedder / Chunker / VectorStore。"""

    def __init__(self, embedder, chunker, store, parser_registry):
        self._embedder = embedder
        self._chunker = chunker
        self._store = store
        self._parser_registry = parser_registry

    def index_file(self, file_path: str, title: str | None = None,
                   doc_metadata: dict | None = None) -> dict:
        """解析文件 → 分块 → 向量化 → 存索引。

        Args:
            file_path: 文件绝对路径（支持 .txt / .pdf 等 ParserRegistry 注册的格式）。
            title: 文档标题，不提供则取文件名（不含扩展名）。
            doc_metadata: 可选元数据，如 {"author": "张三", "year": "2024"}。
        """
        try:
            text = self._parser_registry.parse(file_path)
        except FileNotFoundError:
            return {"error": f"文件未找到: {file_path}"}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"解析文件失败: {e}"}

        if title is None:
            title = Path(file_path).stem

        chunks = self._chunker.chunk(text)
        vectors = self._embedder.encode_documents(chunks)
        total = self._store.add(vectors, chunks, file_path, title=title, doc_metadata=doc_metadata)

        return {
            "status": "indexed",
            "file": file_path,
            "title": title,
            "chunks_indexed": len(chunks),
            "total_chunks_in_store": total,
        }