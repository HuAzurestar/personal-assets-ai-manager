"""Keep a complete short database unit on one worker; drain it at shutdown."""
import asyncio


async def short_database_work(operation, *args):
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # SQLite transactions cannot be interrupted safely by cancelling their
        # asyncio waiter. Keep the Session alive until the bounded unit finishes.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
