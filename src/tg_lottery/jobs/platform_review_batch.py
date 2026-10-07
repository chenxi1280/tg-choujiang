"""Bounded review work; retain successful observations when another request fails."""

import asyncio


async def review_batch(participants, *, review, concurrency=4):
    pending = iter(participants)

    async def worker():
        for participant in pending:
            await review(participant)

    results = await asyncio.gather(*(worker() for _ in range(concurrency)), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
