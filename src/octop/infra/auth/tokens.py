"""Access-token JWT helpers shared by HTTP auth and infra (Bridge)."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

import jwt


class InvalidToken(Exception): ...


class TokenExpired(InvalidToken): ...


def session_id(token: str, payload: dict[str, Any]) -> str:
    """Stable session identity, including tokens issued before session ids existed."""
    return str(payload.get("sid") or hashlib.sha256(token.encode()).hexdigest())


def sign_token(
    secret: bytes,
    *,
    sub: int,
    uname: str,
    role: str,
    ttl_seconds: int = 86400,
    sid: str | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(sub),
        "sid": sid or secrets.token_hex(16),
        "uname": uname,
        "role": role,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(secret: bytes, token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if "sub" in payload:
            payload["sub"] = int(payload["sub"])
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken(str(exc)) from exc
