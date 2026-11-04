"""Keep blocking gateway requests out of the database/progress executor."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial

# The default asyncio executor is also used by MongoDB reads and stage saves.
# A full batch of slow searches must never queue those status operations.
_RETRIEVAL_EXECUTOR = ThreadPoolExecutor(max_workers=12, thread_name_prefix="literature-io")


async def retrieval_io(function, /, *args, **kwargs):
    context = copy_context()
    call = partial(context.run, function, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_RETRIEVAL_EXECUTOR, call)
