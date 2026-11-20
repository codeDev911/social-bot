"""Shared async Redis client.

Redis is optional: when ``REDIS_URL`` is unset every helper reports
"disabled" and the app falls back to running AI work in-process. The client is
cached per event loop because serverless platforms hand each invocation a
fresh loop, and a client bound to a closed loop cannot be reused.
"""

from __future__ import annotations

import asyncio
import logging
import os

try:
    import redis.asyncio as aioredis
except Exception:  # pragma: no cover - redis is pinned in requirements.txt
    aioredis = None

logger = logging.getLogger(__name__)


def _clean(name: str) -> str:
    """Hosting dashboards often paste values with quotes or a trailing newline."""
    return (os.getenv(name) or "").strip().strip("'\"").strip()


REDIS_URL = _clean("REDIS_URL")
DISABLED = _clean("AI_QUEUE_DISABLED").lower() in {"1", "true", "yes", "on"}
CONNECT_TIMEOUT = float(_clean("REDIS_CONNECT_TIMEOUT") or 2)
SOCKET_TIMEOUT = float(_clean("REDIS_SOCKET_TIMEOUT") or 2)

_client = None
_client_loop = None


def redis_enabled() -> bool:
    return bool(REDIS_URL and aioredis is not None and not DISABLED)


async def get_client():
    """Return a client bound to the running loop, or None when disabled."""
    global _client, _client_loop
    if not redis_enabled():
        return None

    loop = asyncio.get_running_loop()
    if _client is not None and _client_loop is loop:
        return _client

    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass

    _client = aioredis.from_url(
        REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=CONNECT_TIMEOUT,
        socket_timeout=SOCKET_TIMEOUT,
        health_check_interval=30,
    )
    _client_loop = loop
    logger.info("Redis connected: %s", _safe_url(REDIS_URL))
    return _client


async def close_client() -> None:
    global _client, _client_loop
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None
    _client_loop = None


def _safe_url(url: str) -> str:
    """Log the host without the password."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"


def status() -> dict:
    """One-line diagnostics for startup logs and the diagnostics endpoint."""
    return {
        "enabled": redis_enabled(),
        "url": _safe_url(REDIS_URL) if REDIS_URL else None,
    }