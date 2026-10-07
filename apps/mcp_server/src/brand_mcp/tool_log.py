import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from brand_mcp.auth import Authorized
from brand_shared.logging_setup import get_logger

log = get_logger("brand_mcp.tools")


@asynccontextmanager
async def tool_call(auth: Authorized, **fields: object) -> AsyncIterator[None]:
    """One structured log line per tool execution, tied to the gateway trace."""
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except Exception as exc:
        outcome = type(exc).__name__
        raise
    finally:
        log.info(
            "tool_call",
            tool=auth.tool.value,
            outcome=outcome,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            trace_id=auth.claims.trace_id,
            user_id=str(auth.claims.user_id),
            role=auth.claims.role.value,
            client_id=str(auth.client_id),
            **fields,
        )
