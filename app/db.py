"""Delt asyncpg-pool (opprettes ved første bruk)."""

from __future__ import annotations

import asyncio

import asyncpg

from app import config

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        async with _lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(config.database_url(), min_size=1, max_size=10)
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
