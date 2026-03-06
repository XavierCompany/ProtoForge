"""Shared HITL wait utilities.

Provides a single helper for selector classes to wait on review events with
optional timeout handling, so timeout semantics are implemented consistently.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


async def wait_for_resolution(
    event: asyncio.Event,
    timeout: float | None,
    *,
    on_timeout: Callable[[], None] | None = None,
) -> bool:
    """Wait for a HITL event to resolve.

    Returns ``True`` when the wait timed out, ``False`` when resolved normally.
    """
    if timeout is None:
        await event.wait()
        return False

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        if on_timeout:
            on_timeout()
        return True

    return False
