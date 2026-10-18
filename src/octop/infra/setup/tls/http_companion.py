"""Minimal HTTP listener on port 80 — ACME challenges + redirect to HTTPS."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Route

from octop.infra.setup.tls.challenge import challenge_store


async def _acme_challenge(request: Request) -> PlainTextResponse:
    token = request.path_params["token"]
    body = challenge_store.get(token)
    if body is None:
        return PlainTextResponse("not found", status_code=404)
    return PlainTextResponse(body)


def build_http_companion_app(*, https_port: int) -> Starlette:
    """Serve HTTP-01 challenges and redirect everything else to HTTPS."""

    async def redirect_https(request: Request) -> RedirectResponse:
        host = (request.headers.get("host") or "localhost").split(":")[0]
        path = request.url.path or "/"
        base = f"https://{host}" if https_port == 443 else f"https://{host}:{https_port}"
        target = f"{base}{path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=301)

    return Starlette(
        routes=[
            Route(
                "/.well-known/acme-challenge/{token}",
                _acme_challenge,
                methods=["GET"],
            ),
            Route("/", redirect_https),
            Route("/{path:path}", redirect_https),
        ]
    )
