"""Cancel and reap a running operation on shutdown before releasing ownership."""

import asyncio
from contextlib import suppress


async def run_until_stopped(operation, stop: asyncio.Event) -> bool:
    processing = asyncio.create_task(operation)
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((processing, stopping), return_when=asyncio.FIRST_COMPLETED)
        if processing not in done:
            processing.cancel()
        try:
            return await processing
        except asyncio.CancelledError:
            if stop.is_set():
                return False
            raise
    finally:
        stopping.cancel()
        with suppress(asyncio.CancelledError):
            await stopping
        if not processing.done():
            processing.cancel()
            with suppress(asyncio.CancelledError):
                await processing
