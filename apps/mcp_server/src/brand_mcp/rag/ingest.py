import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brand_mcp.rag.chunking import chunk_blocks
from brand_mcp.rag.models import Embedder
from brand_mcp.rag.parsing import parse
from brand_shared.db.models import Chunk, Document
from brand_shared.logging_setup import get_logger

log = get_logger("brand_mcp.ingest")


class DocumentTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class IngestResult:
    document_id: uuid.UUID
    title: str
    chunks: int
    duplicate: bool


async def _existing(session: AsyncSession, client_id: uuid.UUID, sha: str) -> Document | None:
    return (
        await session.scalars(
            select(Document).where(Document.client_id == client_id, Document.sha256 == sha)
        )
    ).first()


async def ingest_document(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    *,
    client_id: uuid.UUID,
    filename: str,
    content: bytes,
    uploaded_by: uuid.UUID | None,
    max_bytes: int,
    target_chars: int,
    overlap_chars: int,
) -> IngestResult:
    """Parse -> chunk -> embed -> store. Idempotent per (client, file content)."""
    if len(content) > max_bytes:
        raise DocumentTooLargeError(f"Файл больше {max_bytes // (1024 * 1024)} МБ")
    sha = hashlib.sha256(content).hexdigest()
    async with sessionmaker() as session:
        existing = await _existing(session, client_id, sha)
        if existing is not None:
            return IngestResult(existing.id, existing.title, existing.chunk_count, duplicate=True)

    parsed = parse(filename, content)
    chunks = chunk_blocks(parsed.blocks, target_chars=target_chars, overlap_chars=overlap_chars)
    vectors = await embedder.embed_documents([c.embedding_text() for c in chunks])

    document = Document(
        client_id=client_id,
        filename=filename[:255],
        title=parsed.title[:255],
        content_type=parsed.content_type,
        sha256=sha,
        size_bytes=len(content),
        chunk_count=len(chunks),
        uploaded_by=uploaded_by,
    )
    async with sessionmaker() as session:
        session.add(document)
        await session.flush()
        session.add_all(
            Chunk(
                document_id=document.id,
                client_id=client_id,
                ord=index,
                section=chunk.section[:500],
                text=chunk.text,
                embedding=vector,
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        )
        try:
            await session.commit()
        except IntegrityError:  # the same file uploaded concurrently
            await session.rollback()
            existing = await _existing(session, client_id, sha)
            if existing is None:
                raise
            return IngestResult(existing.id, existing.title, existing.chunk_count, duplicate=True)
    log.info("document_ingested", client_id=str(client_id), title=parsed.title, chunks=len(chunks))
    return IngestResult(document.id, document.title, len(chunks), duplicate=False)
