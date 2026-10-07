"""Internal tool: the gateway's upload endpoint calls it; it is never offered to the model."""

import base64
import binascii
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import Resolve
from mcp.types import ToolAnnotations
from pydantic import Field

from brand_mcp.auth import Authorized, authorize
from brand_mcp.deps import Deps
from brand_mcp.rag.ingest import DocumentTooLargeError, ingest_document
from brand_mcp.rag.parsing import UnsupportedDocumentError
from brand_mcp.tool_log import tool_call
from brand_mcp.tools.common import ClientId
from brand_shared.permissions import Tool
from brand_shared.schemas.tools import IngestDocumentResult

INGESTS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)


def register(server: MCPServer, deps: Deps) -> None:
    secret = deps.settings.mcp_internal_secret.get_secret_value()

    def ingest_auth(ctx: Context, client_id: str) -> Authorized:
        return authorize(ctx, secret, Tool.INGEST_DOCUMENT, client_id)

    @server.tool(name=Tool.INGEST_DOCUMENT.value, annotations=INGESTS)
    async def ingest_document_tool(
        client_id: ClientId,
        filename: Annotated[str, Field(min_length=3, max_length=255)],
        content_base64: Annotated[str, Field(min_length=4)],
        auth: Annotated[Authorized, Resolve(ingest_auth)],
    ) -> IngestDocumentResult:
        """Загружает документ (PDF, DOCX, MD, TXT) в базу знаний клиента: парсинг, чанкинг,
        эмбеддинги. Повторная загрузка того же файла ничего не дублирует."""
        async with tool_call(auth, filename=filename):
            try:
                content = base64.b64decode(content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ToolError("Файл повреждён при передаче") from exc
            try:
                result = await ingest_document(
                    deps.sessionmaker,
                    deps.embedder,
                    client_id=auth.client_id,
                    filename=filename,
                    content=content,
                    uploaded_by=auth.claims.user_id,
                    max_bytes=deps.settings.max_upload_bytes,
                    target_chars=deps.settings.chunk_target_chars,
                    overlap_chars=deps.settings.chunk_overlap_chars,
                )
            except (UnsupportedDocumentError, DocumentTooLargeError) as exc:
                raise ToolError(str(exc)) from exc
            except Exception as exc:  # corrupt PDF/DOCX raise library-specific errors
                raise ToolError("Не удалось прочитать документ") from exc
            return IngestDocumentResult(
                document_id=result.document_id,
                title=result.title,
                chunks=result.chunks,
                duplicate=result.duplicate,
            )
