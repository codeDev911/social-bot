"""
Dispatch downloads to the standalone worker service when one is configured.

Set WORKER_SERVICE_URL + WORKER_TOKEN to move yt-dlp, Deno, cookies and the
Cloudinary upload off the web host. When they are unset every caller keeps using
the in-process pipeline, so this is a pure opt-in.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

WORKER_SERVICE_URL = (os.getenv("WORKER_SERVICE_URL") or "").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
WORKER_TIMEOUT = float(os.getenv("WORKER_DISPATCH_TIMEOUT", "30"))


def worker_enabled() -> bool:
    return bool(WORKER_SERVICE_URL and WORKER_TOKEN)


async def dispatch_download(
    user_id: int,
    video_url: str,
    platform: str = "",
    title: Optional[str] = None,
    external_id: Optional[str] = None,
    upload_destinations: Optional[list[str]] = None,
    transformation: Optional[Any] = None,
) -> bool:
    """
    Queue a download on the worker.

    Returns True when the worker accepted the job, False when no worker is
    configured or the request failed — callers then fall back to local work.
    """
    if not worker_enabled():
        return False

    payload = {
        "userId": user_id,
        "videoUrl": video_url,
        "platform": platform,
        "title": title,
        "externalId": external_id,
        "uploadDestinations": upload_destinations or ["Youtube"],
        "transformation": transformation,
    }

    try:
        async with httpx.AsyncClient(timeout=WORKER_TIMEOUT) as client:
            response = await client.post(
                f"{WORKER_SERVICE_URL}/jobs",
                json=payload,
                headers={"X-Worker-Token": WORKER_TOKEN},
            )
        if response.status_code >= 400:
            logger.warning(
                "Worker rejected %s (%s): %s",
                video_url,
                response.status_code,
                response.text[:300],
            )
            return False
        logger.info(
            "Dispatched %s to worker as job %s",
            video_url,
            response.json().get("jobId"),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker dispatch failed, using local pipeline: %s", exc)
        return False