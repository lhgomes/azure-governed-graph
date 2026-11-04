"""Run finite framework workflows from synchronous or asynchronous callers."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any, TypeVar

T = TypeVar("T")


def run_sync(execution: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execution)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(copy_context().run, asyncio.run, execution).result()
