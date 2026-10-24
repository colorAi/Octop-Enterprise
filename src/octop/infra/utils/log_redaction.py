"""Keep query-string credentials out of HTTP and WebSocket request logs."""

from __future__ import annotations

import logging
import re

_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|access_token|api_key|ticket)=)[^\s&\"']+")


class QuerySecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _QUERY_SECRET.sub(r"\1[redacted]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_query_secret_filter() -> None:
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, QuerySecretFilter) for item in logger.filters):
            logger.addFilter(QuerySecretFilter())
