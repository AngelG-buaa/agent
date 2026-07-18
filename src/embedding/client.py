"""OpenAI-compatible text embedding client."""

import logging

import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)


class Embedder:
    """Encode document batches and queries through an embedding service."""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._use_requests = False
        self._client = OpenAI(api_key=api_key, base_url=self.base_url)
        logger.info("Embedder: using OpenAI SDK for %s", self.base_url)

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        """Encode document texts in batches and return float32 vectors."""
        return self._batched_encode(texts, batch_size=16)

    def encode_query(self, text: str) -> np.ndarray:
        """Encode one query and return a float32 vector."""
        vectors = self._batched_encode([text], batch_size=1)
        return vectors[0]

    def _batched_encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        vectors = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            vectors.extend(self._call_api(batch))
        return np.array(vectors, dtype=np.float32)

    def _call_api(self, batch: list[str]) -> list[list[float]]:
        if self._use_requests or self._client is None:
            return self._call_via_requests(batch)
        try:
            return self._call_via_sdk(batch)
        except Exception as exc:
            if "SSL" in str(exc) or "certificate" in str(exc).lower():
                logger.warning(
                    "OpenAI SDK SSL error, switching to requests+verify=False"
                )
                self._use_requests = True
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                return self._call_via_requests(batch)
            raise

    def _call_via_sdk(self, batch: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(
            model=self.model,
            input=batch,
            encoding_format="float",
        )
        sorted_data = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in sorted_data]

    def _call_via_requests(self, batch: list[str]) -> list[list[float]]:
        import requests

        response = requests.post(
            f"{self.base_url}/embeddings",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            json={
                "model": self.model,
                "input": batch,
                "encoding_format": "float",
            },
            verify=False,
            timeout=60,
        )
        response.raise_for_status()
        sorted_data = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in sorted_data]
