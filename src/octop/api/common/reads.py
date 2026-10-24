"""HTTP adaptation for bounded synchronous management reads."""

from collections.abc import Callable
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.bounded_io import BoundedIO, ReadQueueFull


async def management_read[T](server: Any, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        reader: BoundedIO = server.management_io
        return await reader.run(fn, *args, **kwargs)
    except ReadQueueFull as exc:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            "management read capacity exceeded",
            status=503,
            details={"reason": "read_capacity_exceeded", "retryable": True},
        ) from exc
