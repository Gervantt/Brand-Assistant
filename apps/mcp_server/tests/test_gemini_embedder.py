"""Gemini embeddings: request shape, Matryoshka truncation + re-normalisation, batching."""

import json
import math
from typing import Any

import httpx2

from brand_mcp.rag.models import GeminiEmbedder
from brand_shared.db.models import EMBEDDING_DIM


def fake_gemini(seen: list[dict[str, Any]]) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append({"url": str(request.url), "key": request.headers["x-goog-api-key"], **body})
        # Truncated Gemini vectors are NOT unit length; the client must normalise them.
        return httpx2.Response(
            200,
            json={
                "embeddings": [
                    {"values": [3.0, 4.0] + [0.0] * (EMBEDDING_DIM - 2)} for _ in body["requests"]
                ]
            },
        )

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def test_documents_and_queries_use_retrieval_task_types() -> None:
    seen: list[dict[str, Any]] = []
    embedder = GeminiEmbedder("test-key", "gemini-embedding-001", http=fake_gemini(seen))

    vectors = await embedder.embed_documents(["тон голоса", "цвета"])
    query = await embedder.embed_query("какой тон?")

    assert seen[0]["url"].endswith("/models/gemini-embedding-001:batchEmbedContents")
    assert seen[0]["key"] == "test-key"
    first = seen[0]["requests"][0]
    assert first["taskType"] == "RETRIEVAL_DOCUMENT"
    assert first["outputDimensionality"] == EMBEDDING_DIM
    assert first["content"] == {"parts": [{"text": "тон голоса"}]}
    assert seen[1]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
    assert len(vectors) == 2
    assert math.isclose(math.sqrt(sum(x * x for x in query)), 1.0)
    assert query[:2] == [0.6, 0.8]


async def test_large_ingests_are_split_into_api_sized_batches() -> None:
    seen: list[dict[str, Any]] = []
    embedder = GeminiEmbedder("k", "gemini-embedding-001", http=fake_gemini(seen))
    vectors = await embedder.embed_documents([f"фрагмент {i}" for i in range(230)])
    assert [len(call["requests"]) for call in seen] == [100, 100, 30]
    assert len(vectors) == 230
