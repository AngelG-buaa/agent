"""集中管理配置。"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKDIR = _PROJECT_ROOT  # 工具执行的安全边界，文件写操作不得超出此目录

# 从项目根目录的 .env 加载环境变量（.env 已被 .gitignore 忽略，密钥不入库）
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


def _require_env(name: str) -> str:
    """读取必需的环境变量，缺失时给出明确报错。"""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"环境变量 {name} 未设置。请复制 .env.example 为 .env 并填入真实密钥。"
        )
    return value


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


@dataclass(frozen=True)
class MemoryConfig:
    """Project-level long-term Memory settings."""

    memory_dir: str
    semantic_threshold: float = 0.35
    lexical_threshold: float = 0.25
    recall_top_k: int = 3
    rrf_k: int = 60
    max_context_chars: int = 12_000
    max_record_chars: int = 4_000


llm = LLMConfig(
    api_key=_require_env("DASHSCOPE_API_KEY"),
    base_url="https://ws-3nvfaye9ft7tkruh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    model="qwen3.6-flash",
)


embedding_model = EmbeddingConfig(
    api_key=_require_env("DASHSCOPE_API_KEY"),
    base_url="https://ws-3nvfaye9ft7tkruh.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    model="text-embedding-v4",
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

memory = MemoryConfig(
    memory_dir=os.path.join(_PROJECT_ROOT, ".myagent", "memory"),
)


@dataclass(frozen=True)
class WebSearchConfig:
    search_top_k: int = 3                            # 网页搜索返回条数
    jina_reader_base: str = "https://r.jina.ai"      # Jina Reader API 基础地址


web_search = WebSearchConfig(
    search_top_k=3,
    jina_reader_base="https://r.jina.ai",
)


@dataclass(frozen=True)
class CompactionConfig:
    """上下文压缩管线配置 —— 四层渐进式压缩的各层阈值。

    执行顺序: L3 (budget) → L1 (snip) → L2 (micro) → L4 (compact_history)。
    L3 必须在 L2 之前，因为 L2 会替换掉大内容，L3 需要完整内容来判断。
    """
    context_limit: int = 800_000               # L4 触发阈值（字符数）
    max_messages_snip: int = 100               # L1 触发阈值（消息数）
    head_count: int = 3                        # L1 头部保留消息数
    keep_recent_tool_results: int = 5          # L2 保留最新 tool_result 数
    min_content_length: int = 120              # L2 最小替换内容长度（字符）
    tool_result_budget_bytes: int = 500_000    # L3 单轮 tool 消息总预算（字节）
    persist_threshold: int = 30_000            # L3 单条持久化阈值（字节）
    persist_preview_chars: int = 2_000         # L3 持久化预览长度（字符）
    summary_max_tokens: int = 2_000            # L4 摘要 max_tokens
    summary_retry_count: int = 2               # L4 摘要失败重试次数
    summary_input_cap: int = 80_000            # L4 输入截断上限（字符）


compaction = CompactionConfig()
