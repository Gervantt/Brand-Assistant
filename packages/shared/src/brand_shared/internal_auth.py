"""Short-lived service token the gateway attaches to every MCP request.

It carries the end user's identity and permissions so the MCP server can re-check RBAC/ABAC
on its side (defense in depth) without trusting anything the model produced.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from brand_shared.permissions import Role

AUDIENCE = "brand-mcp"
ALGORITHM = "HS256"
HEADER = "authorization"


@dataclass(frozen=True)
class InternalClaims:
    user_id: uuid.UUID
    role: Role
    client_ids: frozenset[uuid.UUID]
    trace_id: str

    def can_access_client(self, client_id: uuid.UUID) -> bool:
        return self.role is Role.ADMIN or client_id in self.client_ids


def encode_internal_token(claims: InternalClaims, *, secret: str, ttl_s: int = 600) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(claims.user_id),
        "role": claims.role.value,
        "client_ids": sorted(str(c) for c in claims.client_ids),
        "trace_id": claims.trace_id,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_internal_token(token: str, *, secret: str) -> InternalClaims:
    """Raises `jwt.InvalidTokenError` on any problem (bad signature, expiry, audience, shape)."""
    payload = jwt.decode(
        token,
        secret,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        options={"require": ["sub", "role", "exp", "aud"]},
    )
    try:
        return InternalClaims(
            user_id=uuid.UUID(payload["sub"]),
            role=Role(payload["role"]),
            client_ids=frozenset(uuid.UUID(c) for c in payload.get("client_ids", [])),
            trace_id=str(payload.get("trace_id", "")),
        )
    except (ValueError, TypeError) as exc:
        raise jwt.InvalidTokenError(f"malformed claims: {exc}") from exc


def bearer(token: str) -> str:
    return f"Bearer {token}"


def parse_bearer(header_value: str | None) -> str | None:
    if not header_value:
        return None
    scheme, _, token = header_value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None
