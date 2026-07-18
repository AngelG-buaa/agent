"""Project-level long-term Memory domain."""

from memory.models import (
    MemoryChange,
    MemoryMatch,
    MemoryRecall,
    MemoryRecord,
    MemoryType,
)
from memory.retriever import MemoryRetriever
from memory.service import MemoryService
from memory.store import MemoryStore

__all__ = [
    "MemoryChange",
    "MemoryMatch",
    "MemoryRecall",
    "MemoryRecord",
    "MemoryRetriever",
    "MemoryService",
    "MemoryStore",
    "MemoryType",
    "create_memory_service",
]


def create_memory_service():
    """Factory function: assemble and return a fully configured MemoryService.

    Encapsulates the internal component creation logic. Main entry points
    should only need to call this function.

    Returns:
        MemoryService: A fully initialized Memory service with store,
                       retriever, and configuration.

    Note:
        This creates an independent Embedder instance for Memory retrieval.
        If embedding resources are constrained, consider implementing a
        shared Embedder singleton in the embedding module.
    """
    from config import memory as memory_cfg, embedding_model
    from embedding import Embedder

    embedder = Embedder(
        embedding_model.api_key,
        embedding_model.base_url,
        embedding_model.model,
    )
    store = MemoryStore(memory_cfg.memory_dir)
    retriever = MemoryRetriever(embedder, memory_cfg)
    return MemoryService(store, retriever, memory_cfg)
