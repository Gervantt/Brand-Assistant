"""Password hashing (bcrypt) and access tokens (HS256 JWT)."""

import uuid
from datetime import UTC, datetime, timedelta
from functools import cache

import bcrypt
import jwt

ALGORITHM = "HS256"
TOKEN_TYPE = "access"  # noqa: S105 - JWT "type" claim, not a secret
BCRYPT_MAX_BYTES = 72  # bcrypt ignores (v4) or rejects (v5) longer inputs


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


@cache
def _dummy_hash(rounds: int) -> str:
    return hash_password("timing-equaliser-not-a-real-password", rounds)


def burn_password_check(password: str, rounds: int) -> None:
    """Spend the same time as a real check so unknown emails can't be detected by timing."""
    verify_password(password, _dummy_hash(rounds))


def create_access_token(
    user_id: uuid.UUID, *, secret: str, ttl_minutes: int, now: datetime | None = None
) -> tuple[str, datetime]:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(minutes=ttl_minutes)
    payload = {"sub": str(user_id), "type": TOKEN_TYPE, "iat": issued, "exp": expires}
    return jwt.encode(payload, secret, algorithm=ALGORITHM), expires


def decode_access_token(token: str, *, secret: str) -> uuid.UUID:
    """Raises `jwt.InvalidTokenError` (incl. `ExpiredSignatureError`) on any problem."""
    payload = jwt.decode(
        token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp", "type"]}
    )
    if payload.get("type") != TOKEN_TYPE:
        raise jwt.InvalidTokenError("wrong token type")
    try:
        return uuid.UUID(str(payload["sub"]))
    except ValueError as exc:
        raise jwt.InvalidTokenError("malformed subject") from exc
