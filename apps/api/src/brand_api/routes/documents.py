import base64
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select

from brand_api.auth.deps import AccessibleClient, require
from brand_api.auth.principal import Principal
from brand_api.deps import AgentDep, SessionDep, SettingsDep
from brand_shared.db.models import Document
from brand_shared.permissions import Action, Tool

router = APIRouter(prefix="/clients/{client_id}/documents", tags=["documents"])
Uploader = Annotated[Principal, Depends(require(Action.UPLOAD_DOCUMENTS))]
ALLOWED_SUFFIXES = (".pdf", ".docx", ".md", ".markdown", ".txt")


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    title: str
    content_type: str
    size_bytes: int
    chunk_count: int
    created_at: datetime


@router.get("")
async def list_documents(client: AccessibleClient, session: SessionDep) -> list[DocumentOut]:
    rows = (
        await session.scalars(
            select(Document)
            .where(Document.client_id == client.id)
            .order_by(Document.created_at.desc())
        )
    ).all()
    return [DocumentOut.model_validate(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    client: AccessibleClient,
    principal: Uploader,
    agent: AgentDep,
    settings: SettingsDep,
    file: UploadFile,
) -> dict[str, Any]:
    """Upload a brand book or brief. Parsing/embedding happens on the MCP server (the only
    process that loads the embedding model) via the internal `ingest_document` tool."""
    filename = file.filename or "document"
    if not filename.lower().endswith(ALLOWED_SUFFIXES):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Поддерживаются PDF, DOCX, MD и TXT"
        )
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Файл больше {settings.max_upload_bytes // (1024 * 1024)} МБ",
        )
    async with agent.mcp.session(principal, trace_id=uuid.uuid4().hex) as mcp:
        outcome = await mcp.call(
            Tool.INGEST_DOCUMENT.value,
            {
                "client_id": str(client.id),
                "filename": filename,
                "content_base64": base64.b64encode(content).decode(),
            },
        )
    if not outcome.ok or outcome.data is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, outcome.text or "Не удалось загрузить")
    return outcome.data


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, client: AccessibleClient, _: Uploader, session: SessionDep
) -> None:
    result = await session.execute(
        delete(Document).where(Document.id == document_id, Document.client_id == client.id)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    await session.commit()
