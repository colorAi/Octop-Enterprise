"""tests/unit/test_jwt_tokens.py"""

from __future__ import annotations

import pytest

from octop.api.deps import InvalidToken, TokenExpired, decode_token, sign_token


def test_sign_and_decode_roundtrip():
    secret = b"x" * 32
    tok = sign_token(secret, sub=42, uname="alice", role="user", ttl_seconds=60)
    payload = decode_token(secret, tok)
    assert payload["sub"] == 42
    assert payload["uname"] == "alice"
    assert payload["role"] == "user"


def test_decode_rejects_wrong_signature():
    tok = sign_token(b"a" * 32, sub=1, uname="a", role="user")
    with pytest.raises(InvalidToken):
        decode_token(b"b" * 32, tok)


def test_expired_token_raises_TokenExpired():
    secret = b"x" * 32
    tok = sign_token(secret, sub=1, uname="a", role="user", ttl_seconds=-1)
    with pytest.raises(TokenExpired):
        decode_token(secret, tok)


def test_garbage_string_is_invalid():
    with pytest.raises(InvalidToken):
        decode_token(b"x" * 32, "not.a.token")
