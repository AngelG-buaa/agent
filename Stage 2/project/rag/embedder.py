"""文本向量化 —— 华为云 BGE-M3（优先 OpenAI SDK，备选 requests）。

管线角色：③ 向量化层。将 chunk 文本编码为 1024 维向量。
依赖：无内部依赖（叶子模块）。被 indexer 和 retriever 共用。
"""

import logging
import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)


class Embedder:
    """Embedding 服务，封装批量调用。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._use_requests = False
        self._client = OpenAI(api_key=api_key, base_url=self.base_url)
        logger.info("Embedder: using OpenAI SDK for %s", self.base_url)
        
    def encode_documents(self, texts: list[str]) -> np.ndarray:
        """批量编码文档 chunk（用于索引），batch_size=16。"""
        return self._batched_encode(texts, batch_size=16)

    def encode_query(self, text: str) -> np.ndarray:
        """编码单条查询。"""
        vectors = self._batched_encode([text], batch_size=1)
        return vectors[0]

    # ── internal ──────────────────────────────────────────────

    def _batched_encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        vectors = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_vecs = self._call_api(batch)
            vectors.extend(batch_vecs)
        return np.array(vectors, dtype=np.float32)

    def _call_api(self, batch: list[str]) -> list[list[float]]:
        if self._use_requests or self._client is None:
            return self._call_via_requests(batch)
        try:
            return self._call_via_sdk(batch)
        except Exception as e:
            if "SSL" in str(e) or "certificate" in str(e).lower():
                logger.warning("OpenAI SDK SSL error, switching to requests+verify=False")
                self._use_requests = True
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                return self._call_via_requests(batch)
            raise

    def _call_via_sdk(self, batch: list[str]) -> list[list[float]]:
        r = self._client.embeddings.create(
            model=self.model,
            input=batch,
            encoding_format="float",
        )
        sorted_data = sorted(r.data, key=lambda d: d.index)
        return [d.embedding for d in sorted_data]

    def _call_via_requests(self, batch: list[str]) -> list[list[float]]:
        import requests
        url = f"{self.base_url}/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {"model": self.model, "input": batch, "encoding_format": "float"}
        resp = requests.post(url, headers=headers, json=data, verify=False, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        sorted_data = sorted(body["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in sorted_data]
