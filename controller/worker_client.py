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

def _clean(name: str) -> str:
    """Env values pasted into a hosting dashboard often arrive with a trailing
    newline or wrapping quotes; both turn a valid URL/token into a dead one."""
    return (os.getenv(name) or "").strip().strip("'\"").strip()


WORKER_SERVICE_URL = _clean("WORKER_SERVICE_URL").rstrip("/")
WORKER_TOKEN = _clean("WORKER_TOKEN")
WORKER_TIMEOUT = float(_clean("WORKER_DISPATCH_TIMEOUT") or 30)
JOB_STATUS_TIMEOUT = float(_clean("WORKER_STATUS_TIMEOUT") or 3)
WORKER_REQUIRED = (_clean("WORKER_REQUIRED").lower() in {"1", "true", "yes", "on"})

_LAST_DISPATCH_ERROR: Optional[str] = None


def worker_enabled() -> bool:
    return bool(WORKER_SERVICE_URL and WORKER_TOKEN)


def last_dispatch_error() -> Optional[str]:
    """Why the last dispatch failed, verbatim, for the log and the diagnostics page."""
    return _LAST_DISPATCH_ERROR


def running_on_vercel() -> bool:
    """Vercel exposes VERCEL=1 / VERCEL_ENV to every function invocation."""
    return bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))


def worker_required() -> bool:
    """On Vercel a download can only run on the worker: the fallback would be
    killed at maxDuration, so a failed dispatch must surface as an error."""
    return worker_enabled() and (WORKER_REQUIRED or running_on_vercel())


def worker_status() -> dict:
    return {
        "enabled": worker_enabled(),
        "url": WORKER_SERVICE_URL or None,
        "tokenSet": bool(WORKER_TOKEN),
        "required": worker_required(),
        "onVercel": running_on_vercel(),
        "lastDispatchError": _LAST_DISPATCH_ERROR,
    }


async def dispatch_download(
    user_id: int,
    video_url: str,
    platform: str = "",
    title: Optional[str] = None,
    external_id: Optional[str] = None,
    upload_destinations: Optional[list[str]] = None,
    transformation: Optional[Any] = None,
) -> Optional[dict]:
    """
    Queue a download on the worker.

    Returns the worker's job description (``{"jobId": ..., "status": "queued"}``)
    when the job was accepted, or None when no worker is configured or the
    request failed — callers then fall back to local work.
    """
    if not worker_enabled():
        _set_dispatch_error("WORKER_SERVICE_URL / WORKER_TOKEN are not set")
        return None

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
            _set_dispatch_error(f"worker answered HTTP {response.status_code}: {response.text[:200]}")
            return None
        job = response.json()
        logger.info("Dispatched %s to worker as job %s", video_url, job.get("jobId"))
        _set_dispatch_error(None)
        return job
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker dispatch failed: %s", exc)
        _set_dispatch_error(str(exc))
        return None


async def fetch_worker_health() -> dict:
    """Read the worker's /health from the app host - the same hop a job takes."""
    if not worker_enabled():
        return {"reachable": False, "error": "WORKER_SERVICE_URL / WORKER_TOKEN are not set"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{WORKER_SERVICE_URL}/health")
        if response.status_code != 200:
            return {"reachable": False, "error": f"HTTP {response.status_code}: {response.text[:200]}"}
        return {"reachable": True, "health": response.json()}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc)}


def _set_dispatch_error(message: Optional[str]) -> None:
    global _LAST_DISPATCH_ERROR
    _LAST_DISPATCH_ERROR = message


if not worker_enabled():
    logger.info(
        "No download worker configured (WORKER_SERVICE_URL / WORKER_TOKEN empty); "
        "downloads will run in-process%s",
        " on this Vercel function, where they cannot finish" if running_on_vercel() else "",
    )
else:
    logger.info(
        "Download worker configured: %s (required=%s, onVercel=%s)",
        WORKER_SERVICE_URL,
        worker_required(),
        running_on_vercel(),
    )
    if not WORKER_SERVICE_URL.startswith(("http://", "https://")):
        logger.error(
            "WORKER_SERVICE_URL does not start with http(s):// - it was probably "
            "pasted with a label or a typo: %r",
            WORKER_SERVICE_URL,
        )


async def fetch_job_status(job_id: str) -> Optional[dict]:
    """Live status of one worker job; None when the worker is unreachable."""
    if not worker_enabled() or not job_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=JOB_STATUS_TIMEOUT) as client:
            response = await client.get(
                f"{WORKER_SERVICE_URL}/jobs/{job_id}",
                headers={"X-Worker-Token": WORKER_TOKEN},
            )
        if response.status_code != 200:
            return None
        return response.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Worker status unavailable for %s: %s", job_id, exc)
        return None