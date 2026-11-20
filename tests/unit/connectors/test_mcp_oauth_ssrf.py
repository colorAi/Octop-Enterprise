"""Unit tests for MCP OAuth SSRF guards."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from octop.infra.connectors.oauth.mcp import (
    _ensure_mcp_oauth_url,
    _validate_metadata_endpoints,
    build_authorize_url,
    exchange_authorization_code,
    fetch_authorization_metadata,
    issuer_for_kind,
)


@pytest.mark.asyncio
async def test_path_scoped_issuer_discovers_rfc8414_metadata() -> None:
    issuer = "https://mcp.example.org/tenant/oauth"
    metadata = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
    }
    response = AsyncMock()
    response.raise_for_status = lambda: None
    response.json = lambda: metadata
    with (
        patch(
            "octop.infra.connectors.oauth.mcp.safe_request", AsyncMock(return_value=response)
        ) as request,
        patch("octop.infra.connectors.oauth.mcp._ensure_mcp_oauth_url", AsyncMock()),
    ):
        assert await fetch_authorization_metadata(issuer) == metadata
    assert request.await_args.args == (
        "GET",
        "https://mcp.example.org/.well-known/oauth-authorization-server/tenant/oauth",
    )


def test_authorize_url_keeps_endpoint_query_parameters() -> None:
    url = build_authorize_url(
        {"authorization_endpoint": "https://mcp.example.org/authorize?audience=docs&state=old"},
        client_id="app",
        redirect_uri="https://app.example/cb",
        state="current",
        code_challenge="challenge",
    )
    query = parse_qs(urlsplit(url).query)
    assert query["audience"] == ["docs"]
    assert query["state"] == ["current"]


@pytest.mark.asyncio
async def test_ensure_mcp_oauth_url_rejects_internal_token_endpoint() -> None:
    issuer = issuer_for_kind("notion")
    with pytest.raises(ValueError, match="private or reserved"):
        await _ensure_mcp_oauth_url(
            "https://127.0.0.1/oauth/token",
            issuer=issuer,
            field="token_endpoint",
        )


@pytest.mark.asyncio
async def test_ensure_mcp_oauth_url_rejects_foreign_host() -> None:
    issuer = issuer_for_kind("notion")
    with pytest.raises(ValueError, match="host is not allowed"):
        await _ensure_mcp_oauth_url(
            "https://evil.example.com/oauth/token",
            issuer=issuer,
            field="token_endpoint",
        )


@pytest.mark.asyncio
async def test_validate_metadata_endpoints_rejects_poisoned_metadata() -> None:
    issuer = issuer_for_kind("notion")
    metadata = {
        "authorization_endpoint": "https://mcp.notion.com/authorize",
        "token_endpoint": "http://169.254.169.254/",
    }
    with pytest.raises(ValueError):
        await _validate_metadata_endpoints(metadata, issuer=issuer)


@pytest.mark.asyncio
async def test_exchange_authorization_code_uses_validated_url() -> None:
    issuer = issuer_for_kind("notion")
    metadata = {
        "authorization_endpoint": "https://mcp.notion.com/authorize",
        "token_endpoint": "https://mcp.notion.com/token",
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {"access_token": "tok", "expires_in": 3600}

    with (
        patch(
            "octop.infra.connectors.oauth.mcp.safe_request",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ) as mock_request,
        patch(
            "octop.infra.connectors.oauth.mcp._ensure_mcp_oauth_url",
            new_callable=AsyncMock,
            return_value="https://mcp.notion.com/token",
        ) as mock_validate,
    ):
        out = await exchange_authorization_code(
            metadata,
            issuer=issuer,
            client_id="cid",
            client_secret=None,
            code="code",
            redirect_uri="https://app.example/cb",
            code_verifier="verifier",
        )

    assert out["access_token"] == "tok"
    mock_validate.assert_awaited_once()
    mock_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_request_blocks_internal_ip() -> None:
    from octop.infra.utils.ssrf_guard import UnsafeOutboundUrl, safe_request

    with pytest.raises(UnsafeOutboundUrl, match="private or reserved"):
        await safe_request("POST", "https://127.0.0.1/token", data={})
