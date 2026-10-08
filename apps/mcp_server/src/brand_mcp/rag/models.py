"""Embedding and reranking backends behind small protocols (fastembed local / Gemini API)."""

import math
import threading
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import anyio
import httpx2

from brand_shared.db.models import EMBEDDING_DIM


class Embedder(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """Relevance probabilities in [0, 1], one per text, same order."""
        ...


# Small batches keep onnxruntime's peak allocations low (Render's free tier has 512 MB).
EMBED_BATCH_SIZE = 8


class FastembedEmbedder:
    """Local ONNX model (no API key). Loaded lazily; inference runs in a worker thread."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()  # warm-up and startup ingestion race to load the model

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # noqa: PLC0415 - heavy, load on demand

                self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir, threads=1)
            return self._model

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        def run() -> list[list[float]]:
            vectors = self._load().passage_embed(list(texts), batch_size=EMBED_BATCH_SIZE)
            return [v.tolist() for v in vectors]

        return await anyio.to_thread.run_sync(run)

    async def embed_query(self, text: str) -> list[float]:
        def run() -> list[float]:
            vector: list[float] = next(iter(self._load().query_embed(text))).tolist()
            return vector

        return await anyio.to_thread.run_sync(run)


class GeminiEmbedder:
    """Gemini embeddings truncated (Matryoshka) to our column size and re-normalised.

    On the free tier every text in a batch counts as one request (~100/min), so batches are
    small and 429/5xx answers are retried with backoff (honouring Retry-After).
    """

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
    BATCH_SIZE = 20
    MAX_ATTEMPTS = 6

    def __init__(
        self,
        api_key: str,
        model: str,
        http: httpx2.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.http = http or httpx2.AsyncClient(timeout=30)
        self._sleep = sleep

    async def _embed(self, texts: Sequence[str], task: str) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": task,
                    "outputDimensionality": EMBEDDING_DIM,
                }
                for t in texts
            ]
        }
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            response = await self.http.post(
                self.URL.format(model=self.model),
                json=body,
                headers={"x-goog-api-key": self.api_key},
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.MAX_ATTEMPTS:
                break
            await self._sleep(_retry_delay(response, attempt))
        response.raise_for_status()
        return [_normalise(e["values"]) for e in response.json()["embeddings"]]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            vectors.extend(await self._embed(batch, "RETRIEVAL_DOCUMENT"))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "RETRIEVAL_QUERY"))[0]


class FastembedReranker:
    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: PLC0415

                self._model = TextCrossEncoder(self.model_name, cache_dir=self.cache_dir)
            return self._model

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        def run() -> list[float]:
            return [_sigmoid(float(s)) for s in self._load().rerank(query, list(texts))]

        return await anyio.to_thread.run_sync(run)


def _retry_delay(response: httpx2.Response, attempt: int) -> float:
    header = response.headers.get("retry-after", "")
    if header.replace(".", "", 1).isdigit():
        return min(float(header), 60.0)
    return min(2.0**attempt, 60.0)  # 2, 4, 8, 16, 32 s


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
