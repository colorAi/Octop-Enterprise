import base64
import json
from email.parser import BytesParser
from email.policy import default
from unittest.mock import AsyncMock

import httpx
import pytest
from octop_harness.config import MediaProviderConfig
from octop_harness.media.errors import MediaGenerationError
from octop_harness.media.models import ImageGenerationRequest
from octop_harness.media.registry import create_media_provider

from octop.infra.agents.providers.media_openai import register_openai_media_provider


def multipart_fields(request):
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + request.content
    )
    return [
        (part.get_param("name", header="content-disposition"), part.get_payload(decode=True))
        for part in message.iter_parts()
    ]


@pytest.mark.asyncio
async def test_image_edit_uploads_exact_originals_as_multipart_without_text_fallback():
    originals = [b"\x89PNG\r\n\x1a\nfirst-original", b"\xff\xd8\xffsecond-original"]
    calls = []

    def gateway(request):
        calls.append(request)
        assert request.url.path == "/v1/images/edits"
        fields = multipart_fields(request)
        assert [value for key, value in fields if key == "image[]"] == originals
        assert dict(fields)["prompt"] == b"change the light"
        assert dict(fields)["size"] == b"1536x1024"
        assert request.headers["authorization"] == "Bearer test"
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(originals[0]).decode()}]}
        )

    register_openai_media_provider()
    config = MediaProviderConfig(
        id="company", provider="openai", api_key="test", base_url="http://gateway.test/v1"
    )
    references = tuple(
        f"data:{mime};base64,{base64.b64encode(data).decode()}"
        for mime, data in zip(["image/png", "image/jpeg"], originals, strict=True)
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
        provider = create_media_provider(config, client=client)
        assert provider.capabilities.image_references
        result = await provider.generate_image(
            ImageGenerationRequest(
                prompt="change the light", size="1536x1024", reference_images=references
            )
        )
    assert result[0].data == originals[0]
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "transport"])
async def test_failed_image_edit_does_not_retry_or_fall_back_to_generation(failure):
    calls = []

    def gateway(request):
        calls.append(request)
        assert request.url.path.endswith("/images/edits")
        if failure == "transport":
            raise httpx.ReadTimeout("upstream timed out", request=request)
        return httpx.Response(502, json={"error": {"code": "upstream_failed"}})

    register_openai_media_provider()
    config = MediaProviderConfig(id="company", provider="openai", api_key="test")
    reference = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\noriginal").decode()
    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
        provider = create_media_provider(config, client=client)
        with pytest.raises(MediaGenerationError):
            await provider.generate_image(
                ImageGenerationRequest(prompt="edit", reference_images=(reference,))
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_gateway_url_is_downloaded_and_only_download_is_retried(monkeypatch):
    image = b"\x89PNG\r\n\x1a\ngenerated-content"
    requests = []

    def gateway(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/v1/images/generations"
            assert json.loads(request.content)["response_format"] == "url"
            return httpx.Response(200, json={"data": [{"url": "https://images.test/output.png"}]})
        assert str(request.url) == "https://images.test/output.png"
        assert "Authorization" not in request.headers
        if len(requests) == 2:
            raise httpx.ConnectTimeout("temporary connect failure", request=request)
        return httpx.Response(200, content=image)

    register_openai_media_provider()
    monkeypatch.setattr("octop.infra.agents.providers.media_openai.asyncio.sleep", AsyncMock())
    config = MediaProviderConfig(
        id="company", provider="openai", api_key="test", base_url="http://gateway.test/v1"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
        provider = create_media_provider(config, client=client)
        result = await provider.generate_image(ImageGenerationRequest(prompt="test", size="1K"))
    assert result[0].data == image
    assert [request.method for request in requests] == ["POST", "GET", "GET"]
    assert json.loads(requests[0].content)["size"] == "1024x1024"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected_downloads"), [(503, 3), (404, 1)])
async def test_download_failure_has_bounded_retries_and_no_generation_replay(
    monkeypatch, status, expected_downloads
):
    requests = []

    def gateway(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://images.test/output.png"}]})
        return httpx.Response(status)

    register_openai_media_provider()
    monkeypatch.setattr("octop.infra.agents.providers.media_openai.asyncio.sleep", AsyncMock())
    config = MediaProviderConfig(id="company", provider="openai", api_key="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
        provider = create_media_provider(config, client=client)
        with pytest.raises(MediaGenerationError) as error:
            await provider.generate_image(ImageGenerationRequest(prompt="test"))
        assert error.value.safe_to_resubmit is False
        assert error.value.remediation == "retry_download"
    assert [r.method for r in requests].count("POST") == 1
    assert [r.method for r in requests].count("GET") == expected_downloads
