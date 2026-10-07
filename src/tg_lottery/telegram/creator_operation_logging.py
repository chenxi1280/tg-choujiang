import logging
from functools import wraps
from time import perf_counter


def log_creator_action(handler):
    @wraps(handler)
    async def logged(creator, query, grant):
        started = perf_counter()
        completed = False
        try:
            await handler(creator, query, grant)
            completed = True
        finally:
            logging.getLogger(handler.__module__).info(
                "creator action finished",
                extra={
                    "action": grant.action,
                    "elapsed_ms": round((perf_counter() - started) * 1000),
                    "completed": completed,
                },
            )

    return logged
