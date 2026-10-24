import logging

import pytest

from octop.infra.utils.log_redaction import QuerySecretFilter


@pytest.mark.parametrize(
    "message,args",
    [
        ('%s - "WebSocket %s" [accepted]', ("client", "/chat/ws?token=jwt-sentinel&cursor=3")),
        ('%s - "%s %s HTTP/%s" %d', ("client", "GET", "/device?token=jwt-sentinel", "1.1", 401)),
        (
            'HTTP Request: GET "https://example.test/?access_token=jwt-sentinel&api_key=key-sentinel"',
            (),
        ),
    ],
)
def test_request_logs_redact_credentials_without_losing_status(message, args):
    record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, message, args, None)
    assert QuerySecretFilter().filter(record)
    text = record.getMessage()
    assert "jwt-sentinel" not in text and "key-sentinel" not in text
    assert "[redacted]" in text
    if "cursor" in message % args:
        assert "cursor=3" in text
