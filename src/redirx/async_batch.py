"""Bound outstanding tasks as well as active HTTP requests."""
import asyncio


async def bounded_map(function, values, concurrency):
    values = list(values)
    results = [None] * len(values)
    pending = iter(enumerate(values))

    async def worker():
        for index, value in pending:
            results[index] = await function(value)

    async with asyncio.TaskGroup() as group:
        for _ in range(min(concurrency, len(values))):
            group.create_task(worker())
    return results
