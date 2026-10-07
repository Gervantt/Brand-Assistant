import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from brand_api.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SECRET = "x" * 40


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("correct horse", rounds=4)
    assert hashed != "correct horse"
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong horse", hashed)


def test_overlong_password_is_rejected_not_truncated() -> None:
    hashed = hash_password("a" * 72, rounds=4)
    assert not verify_password("a" * 73, hashed)


def test_garbage_hash_does_not_crash() -> None:
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    token, expires = create_access_token(user_id, secret=SECRET, ttl_minutes=5)
    assert decode_access_token(token, secret=SECRET) == user_id
    assert expires > datetime.now(UTC)


def test_expired_token_is_rejected() -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    token, _ = create_access_token(uuid.uuid4(), secret=SECRET, ttl_minutes=5, now=past)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret=SECRET)


def test_token_signed_with_other_secret_is_rejected() -> None:
    token, _ = create_access_token(uuid.uuid4(), secret="y" * 40, ttl_minutes=5)
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token, secret=SECRET)


def test_alg_none_token_is_rejected() -> None:
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "exp": 9_999_999_999},
        key=None,
        algorithm="none",
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(forged, secret=SECRET)


def test_token_without_access_type_is_rejected() -> None:
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "refresh", "exp": 9_999_999_999},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError, match="wrong token type"):
        decode_access_token(token, secret=SECRET)
