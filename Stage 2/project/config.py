"""集中管理配置。"""

import os
from dataclasses import dataclass

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKDIR = _PROJECT_ROOT  # 工具执行的安全边界，文件写操作不得超出此目录


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class EmbeddingConfig:
    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class RAGConfig:
    chunk_token_size: int
    overlapped_percent: float
    top_k: int
    index_dir: str
    store_backend: str                          # "faiss" | "qdrant"
    qdrant_url: str | None = None               # Qdrant 服务地址，如 "http://localhost:6333"
    qdrant_collection: str = "rag_documents"      # Qdrant 集合名称


llm = LLMConfig(
    api_key="2sbqJpYViTl8cuoEiO-8N8Ydn228GHvA_E0ZZ-qql4gTrz7lXwYlM4rUOX9i9Z3YbO5CB4ajmvgMYD27Ti9U_w",
    base_url="https://api.modelarts-maas.com/openai/v1",
    model="deepseek-v4-pro",
)

# llm = LLMConfig(
#     api_key="sk-4b05e846d5974843bc68c93a9f9baef3",
#     base_url="https://api.deepseek.com",
#     model="deepseek-v4-pro",
# )

embedding_model = EmbeddingConfig(
    api_key="2sbqJpYViTl8cuoEiO-8N8Ydn228GHvA_E0ZZ-qql4gTrz7lXwYlM4rUOX9i9Z3YbO5CB4ajmvgMYD27Ti9U_w",
    base_url="https://api.modelarts-maas.com/v1",
    model="bge-m3",
)

rag = RAGConfig(
    chunk_token_size=128,
    overlapped_percent=0.1,
    top_k=3,
    index_dir=os.path.join(_PROJECT_ROOT, "rag", "data", "index"),
    store_backend="faiss",
    qdrant_url="http://localhost:6333",
    qdrant_collection = "rag_documents",
)


@dataclass(frozen=True)
class WebSearchConfig:
    search_top_k: int = 3                            # 网页搜索返回条数
    jina_reader_base: str = "https://r.jina.ai"      # Jina Reader API 基础地址


web_search = WebSearchConfig(
    search_top_k=3,
    jina_reader_base="https://r.jina.ai",
)