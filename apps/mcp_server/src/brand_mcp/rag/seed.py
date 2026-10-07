"""Startup ingestion of the demo brand books: data/brands/<client-slug>/*.md.

Runs in the background and waits for the API's seed to create the clients (the two services
start independently). Ingestion is idempotent, so restarts are cheap.
"""

from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from brand_mcp.deps import Deps
from brand_mcp.rag.ingest import ingest_document
from brand_shared.db.models import Client
from brand_shared.logging_setup import get_logger

log = get_logger("brand_mcp.seed")


def _brand_files(directory: Path) -> dict[str, list[tuple[str, bytes]]]:
    if not directory.is_dir():
        return {}
    return {
        folder.name: [(f.name, f.read_bytes()) for f in sorted(folder.glob("*.md"))]
        for folder in sorted(directory.iterdir())
        if folder.is_dir()
    }


async def seed_documents(
    deps: Deps, directory: Path, *, attempts: int = 60, delay_s: float = 3
) -> int:
    pending = await anyio.to_thread.run_sync(_brand_files, directory)
    ingested = 0
    for _ in range(attempts):
        try:
            async with deps.sessionmaker() as session:
                clients = {
                    c.slug: c.id
                    for c in (
                        await session.scalars(select(Client).where(Client.slug.in_(pending)))
                    ).all()
                }
        except DBAPIError as exc:  # schema not migrated yet / database still starting
            log.info("seed_documents_waiting", reason=type(exc.orig).__name__)
            await anyio.sleep(delay_s)
            continue
        for slug, client_id in clients.items():
            for filename, content in pending.pop(slug):
                result = await ingest_document(
                    deps.sessionmaker,
                    deps.embedder,
                    client_id=client_id,
                    filename=filename,
                    content=content,
                    uploaded_by=None,
                    max_bytes=deps.settings.max_upload_bytes,
                    target_chars=deps.settings.chunk_target_chars,
                    overlap_chars=deps.settings.chunk_overlap_chars,
                )
                ingested += 0 if result.duplicate else 1
        if not pending:
            break
        await anyio.sleep(delay_s)
    if pending:
        log.warning("seed_documents_clients_missing", slugs=sorted(pending))
    log.info("seed_documents_complete", ingested=ingested)
    return ingested
