"""Lightweight async helpers shared by server and local runtimes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)
STREAM_CLOSE_TIMEOUT = 5.0


async def close_event_stream(stream: AsyncIterator[Any]) -> None:
    """Close a terminal producer without waiting indefinitely for its finalizers.

    Adapted from octop-gateway 1.0.3 while the enterprise runtime stays pinned
    to its tested dependencies. Callers release running state before closing.
    """
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        async with asyncio.timeout(STREAM_CLOSE_TIMEOUT):
            await close()
    except TimeoutError:
        logger.warning("Event stream cleanup timed out")
    except Exception:
        logger.exception("Event stream cleanup failed")


async def offload[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await asyncio.get_running_loop().run_in_executor(None, partial(fn, *args, **kwargs))
