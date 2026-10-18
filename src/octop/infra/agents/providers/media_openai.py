"""OpenAI-compatible image adapter owned by Octop, including LAN gateways."""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from octop_harness.media.errors import MediaGenerationError
from octop_harness.media.http import (
    BearerJsonMediaProvider,
    detect_media,
    provider_payload_error,
    response_error,
)
from octop_harness.media.models import (
    ImageGenerationRequest,
    MediaCapabilities,
    MediaSubmission,
    RemoteArtifact,
)


class OpenAIImageProvider(BearerJsonMediaProvider):
    provider_id = "openai"
    capabilities = MediaCapabilities(image_references=True)

    async def _submit_image(
        self, client: httpx.AsyncClient, request: ImageGenerationRequest
    ) -> MediaSubmission:
        body: dict[str, Any] = {
            "model": self.image_model,
            "prompt": request.prompt,
            "n": request.count,
            "response_format": "url",
        }
        if request.size:
            body["size"] = {"1K": "1024x1024", "2K": "2048x2048", "4K": "3840x2160"}.get(
                request.size.upper(), request.size
            )
        if request.reference_images:
            # GPT image edits use multipart originals and may return base64.
            # Do not silently fall back to text generation if editing fails.
            body.pop("response_format")
            payload = await self._request_edit(client, body, request.reference_images)
        else:
            payload = await self._request_json(client, "POST", "/images/generations", json=body)
        if payload.get("error"):
            raise provider_payload_error(payload["error"], fallback="Image generation failed")
        artifacts = []
        for row in payload.get("data") or []:
            if not isinstance(row, Mapping):
                continue
            if row.get("b64_json"):
                try:
                    data = base64.b64decode(row["b64_json"], validate=True)
                except (ValueError, TypeError) as exc:
                    raise MediaGenerationError("Image provider returned invalid base64") from exc
                artifacts.append(RemoteArtifact(data=data, media_type="image/png", extension="png"))
            elif row.get("url"):
                artifacts.append(
                    RemoteArtifact(url=row["url"], media_type="image/png", extension="png")
                )
        if not artifacts:
            raise MediaGenerationError("Image provider returned no usable images")
        return MediaSubmission(artifacts=tuple(artifacts))

    async def _request_edit(
        self, client: httpx.AsyncClient, body: dict[str, Any], references: tuple[str, ...]
    ) -> dict[str, Any]:
        files = []
        total = 0
        if len(references) > 4:
            raise MediaGenerationError("At most four reference images are supported")
        for index, reference in enumerate(references):
            match = re.fullmatch(
                r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)", reference
            )
            if not match:
                raise MediaGenerationError(
                    "Reference images must be workspace PNG, JPEG or WebP files"
                )
            try:
                content = base64.b64decode(match[2], validate=True)
            except ValueError as exc:
                raise MediaGenerationError("Reference image has invalid base64") from exc
            total += len(content)
            mime, extension = detect_media(content, fallback_type="", fallback_extension="")
            if not content or mime != match[1] or total > 10 * 1024 * 1024:
                raise MediaGenerationError("Reference images are invalid or total more than 10 MB")
            files.append(("image[]", (f"reference-{index}.{extension}", content, mime)))
        headers = self._auth_headers()
        headers.pop("Content-Type", None)  # httpx supplies the multipart boundary.
        try:
            response = await client.post(
                f"{self._config.base_url.rstrip('/')}/images/edits",
                data={key: str(value) for key, value in body.items()},
                files=files,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise MediaGenerationError(
                "The image editing provider request failed.",
                code="provider_timeout"
                if isinstance(exc, httpx.TimeoutException)
                else "provider_unavailable",
                retryable=True,
                safe_to_resubmit=False,
                remediation="retry_later",
                model_instruction="Do not retry or switch to text generation automatically. Tell the user the image edit failed.",
            ) from exc
        if response.is_error:
            raise response_error(response, provider_task_id=None)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MediaGenerationError("Image editing provider returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MediaGenerationError("Image editing provider returned an unexpected response")
        return payload

    @asynccontextmanager
    async def _client_scope(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._config.request_timeout_seconds, connect=10)
        ) as client:
            yield client

    @staticmethod
    async def _download(client: httpx.AsyncClient, url: str) -> bytes:
        # Retry only retrieval of the existing output, never the paid generation.
        for attempt in range(3):
            try:
                async with asyncio.timeout(60):
                    return await BearerJsonMediaProvider._download(client, url)
            except (MediaGenerationError, TimeoutError) as exc:
                if isinstance(exc, MediaGenerationError):
                    if exc.remediation != "retry_download" or not exc.retryable:
                        raise
                    error = exc
                else:
                    error = MediaGenerationError(
                        "Generated media download timed out.",
                        code="provider_timeout",
                        retryable=True,
                        safe_to_resubmit=False,
                        remediation="retry_download",
                    )
                if attempt == 2:
                    raise error from exc
                await asyncio.sleep(attempt + 1)
        raise AssertionError("unreachable")

    async def _submit_video(self, client: Any, request: Any) -> Any:
        raise MediaGenerationError("Video generation is not enabled for this image provider")

    async def _poll_task(self, client: Any, task: Any) -> Any:
        raise MediaGenerationError("This image provider returns images directly")


def register_openai_media_provider() -> None:
    from octop.infra.utils.harness_compat import require_supported_harness

    require_supported_harness()
    from octop_harness.config import _MEDIA_PROVIDER_DEFAULTS
    from octop_harness.media.registry import BUILTIN_MEDIA_PROVIDERS

    if not isinstance(_MEDIA_PROVIDER_DEFAULTS, dict) or not isinstance(
        BUILTIN_MEDIA_PROVIDERS, dict
    ):
        raise RuntimeError("Harness media registry contract changed")

    # Harness 1.0.2 validates provider names before the registry is consulted.
    # Keep this compatibility entry with its adapter instead of image-only patches.
    _MEDIA_PROVIDER_DEFAULTS.setdefault(
        "openai", ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-image-2", "")
    )
    BUILTIN_MEDIA_PROVIDERS["openai"] = OpenAIImageProvider
