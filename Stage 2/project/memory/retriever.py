"""Hybrid semantic and lexical retrieval for project Memory."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

import numpy as np

from memory.models import MemoryMatch, MemoryRecord

if TYPE_CHECKING:
    from config import MemoryConfig
    from embedding import Embedder

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")


def canonical_text(record: MemoryRecord) -> str:
    """Return the one canonical representation indexed by both channels."""
    return (
        f"Name: {record.name}\n"
        f"Type: {record.memory_type.value}\n"
        f"Description: {record.description}\n"
        f"Content: {record.body}"
    )


class MemoryRetriever:
    """Recall Memory records with hybrid retrieval and RRF ranking."""

    def __init__(self, embedder: Embedder, config: MemoryConfig):
        self._embedder = embedder
        self._config = config
        self._fingerprints: dict[str, str] = {}
        self._vectors: dict[str, np.ndarray] = {}

    def retrieve(
        self,
        query: str,
        records: list[MemoryRecord],
    ) -> tuple[list[MemoryMatch], tuple[str, ...]]:
        """Return fused top matches and any semantic-degradation warning."""
        if not query.strip() or not records:
            return [], ()

        lexical_scores = {
            record.name: self._lexical_score(query, canonical_text(record))
            for record in records
        }
        lexical_ranking = self._rank_above_threshold(
            lexical_scores,
            self._config.lexical_threshold,
        )

        warnings: list[str] = []
        semantic_scores: dict[str, float] = {}
        semantic_ranking: list[str] = []
        try:
            self._sync_vectors(records)
            query_vector = np.asarray(
                self._embedder.encode_query(query),
                dtype=np.float32,
            )
            semantic_scores = {
                record.name: self._cosine(query_vector, self._vectors[record.name])
                for record in records
            }
            semantic_ranking = self._rank_above_threshold(
                semantic_scores,
                self._config.semantic_threshold,
            )
        except Exception as exc:
            warnings.append(f"Semantic recall unavailable; used lexical recall: {exc}")

        semantic_rank = {name: rank for rank, name in enumerate(semantic_ranking, 1)}
        lexical_rank = {name: rank for rank, name in enumerate(lexical_ranking, 1)}
        candidates = set(semantic_rank) | set(lexical_rank)
        records_by_name = {record.name: record for record in records}

        matches = []
        for name in candidates:
            score = 0.0
            if name in semantic_rank:
                score += 1.0 / (self._config.rrf_k + semantic_rank[name])
            if name in lexical_rank:
                score += 1.0 / (self._config.rrf_k + lexical_rank[name])
            matches.append(
                MemoryMatch(
                    record=records_by_name[name],
                    semantic_score=(
                        semantic_scores[name] if name in semantic_rank else None
                    ),
                    lexical_score=(lexical_scores[name] if name in lexical_rank else None),
                    rrf_score=score,
                )
            )

        matches.sort(key=lambda match: (-match.rrf_score, match.record.name))
        return matches[:self._config.recall_top_k], tuple(warnings)

    def _sync_vectors(self, records: list[MemoryRecord]) -> None:
        active_names = {record.name for record in records}
        for stale_name in set(self._vectors) - active_names:
            self._vectors.pop(stale_name, None)
            self._fingerprints.pop(stale_name, None)

        changed = []
        changed_texts = []
        for record in records:
            text = canonical_text(record)
            fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if self._fingerprints.get(record.name) != fingerprint:
                changed.append((record.name, fingerprint))
                changed_texts.append(text)

        if not changed:
            return
        vectors = np.asarray(
            self._embedder.encode_documents(changed_texts),
            dtype=np.float32,
        )
        if len(vectors) != len(changed):
            raise ValueError("Embedder returned an unexpected vector count")
        for (name, fingerprint), vector in zip(changed, vectors, strict=True):
            self._vectors[name] = vector
            self._fingerprints[name] = fingerprint

    @staticmethod
    def _rank_above_threshold(
        scores: dict[str, float],
        threshold: float,
    ) -> list[str]:
        candidates = (
            (name, score) for name, score in scores.items() if score >= threshold
        )
        return [
            name
            for name, _ in sorted(candidates, key=lambda item: (-item[1], item[0]))
        ]

    @classmethod
    def _lexical_score(cls, query: str, text: str) -> float:
        query_tokens = cls._tokens(query)
        if not query_tokens:
            return 0.0
        text_tokens = cls._tokens(text)
        return len(query_tokens & text_tokens) / len(query_tokens)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        tokens: set[str] = set()
        for part in _TOKEN_PATTERN.findall(text.lower()):
            if part.isascii():
                tokens.add(part)
            elif len(part) == 1:
                tokens.add(part)
            else:
                # CJK bigrams preserve useful word adjacency without a tokenizer dependency.
                tokens.update(part[index:index + 2] for index in range(len(part) - 1))
        return tokens

    @staticmethod
    def _cosine(left: np.ndarray, right: np.ndarray) -> float:
        left = np.asarray(left, dtype=np.float32).reshape(-1)
        right = np.asarray(right, dtype=np.float32).reshape(-1)
        if left.shape != right.shape:
            raise ValueError("Embedding dimensions do not match")
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator == 0.0:
            return 0.0
        return float(np.dot(left, right) / denominator)
