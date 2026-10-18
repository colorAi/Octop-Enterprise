"""Voice probe / Tencent error copy."""

from __future__ import annotations

import httpx

from octop.i18n.domains.voice import format_voice_probe_error, tencent_api_language


def test_tencent_secret_id_is_localized() -> None:
    exc = RuntimeError("AuthFailure.SecretIdNotFound: The SecretId is not found.")
    assert format_voice_probe_error(exc, "en") == (
        "SecretId was not found. Check that the key is correct."
    )
    assert format_voice_probe_error(exc, "zh") == "SecretId 不存在，请检查密钥是否填写正确。"


def test_unknown_provider_text_is_kept() -> None:
    exc = RuntimeError("SomeVendor.NewCode: unexplained boom")
    assert format_voice_probe_error(exc, "zh") == "SomeVendor.NewCode: unexplained boom"


def test_http_and_network_errors_are_localized() -> None:
    request = httpx.Request("POST", "https://asr.tencentcloudapi.com/")
    response = httpx.Response(500, request=request)
    status = httpx.HTTPStatusError("server error", request=request, response=response)
    assert format_voice_probe_error(status, "zh") == "服务商返回 HTTP 500"
    assert format_voice_probe_error(httpx.ConnectError("boom"), "zh") == "网络错误：ConnectError"


def test_tencent_api_language_header() -> None:
    assert tencent_api_language("zh") == "zh-CN"
    assert tencent_api_language("en") == "en-US"
    assert tencent_api_language(None) is None
