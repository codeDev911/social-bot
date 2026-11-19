"""
Standalone download worker.

Runs yt-dlp (with Deno + ffmpeg + per-platform cookies), pushes the result to
Cloudinary and reports back to the web app through a signed webhook. It touches
no database, so it can be scaled horizontally as its own container.

Deploy from the same image as the app with a different command:

    uvicorn worker_service:app --host 0.0.0.0 --port 8080

Environment:
    WORKER_TOKEN        shared secret the web app sends in X-Worker-Token
    INTERNAL_SECRET     same value as the web app; signs the callback body
    APP_API_URL         base URL of the web app (callback target)
    CLOUDINARY_*        Cloudinary credentials
    COOKIES_*_B64       Netscape cookies per platform
    PROXY_*             proxy entrypoints / credentials
    YTDLP_*             downloader tuning (see controller/video_downloader.py)

Jobs live in memory: a restart forgets in-flight work. Run one replica per
request-lane and let the web app retry on webhook failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import cloudinary
import cloudinary.uploader
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from controller.video_downloader import (
    COOKIE_ENV,
    CookiesRequiredError,
    JSRuntimeMissingError,
    cookie_file_for,
    download_video,
    ffmpeg_path,
    js_runtime_status,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("download-worker")

WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")
APP_API_URL = (os.getenv("APP_API_URL") or os.getenv("FRONTEND_URL") or "").rstrip("/")
CALLBACK_PATH = os.getenv("WORKER_CALLBACK_PATH", "/dashboard/api/worker/ingest")

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

app = FastAPI(title="download-worker")

JOBS: dict[str, dict[str, Any]] = {}
JOB_LOCK = asyncio.Lock()


class JobRequest(BaseModel):
    userId: int
    videoUrl: str
    platform: str = ""
    title: Optional[str] = None
    externalId: Optional[str] = None
    uploadDestinations: list[str] = ["Youtube"]
    transformation: Optional[Any] = None
    callbackUrl: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sign(body: bytes) -> str:
    return hmac.new(INTERNAL_SECRET.encode(), body, hashlib.sha256).hexdigest()


async def _set_job(job_id: str, **fields: Any) -> None:
    async with JOB_LOCK:
        JOBS.setdefault(job_id, {}).update(fields)


async def _notify(payload: dict, callback_url: Optional[str]) -> bool:
    """POST the finished upload back to the app with an HMAC over the raw body."""
    if not INTERNAL_SECRET:
        logger.error("INTERNAL_SECRET is not set — webhook cannot be signed")
        return False
    url = callback_url or f"{APP_API_URL}{CALLBACK_PATH}"
    if not url.startswith("http"):
        logger.error("No callback URL available (set APP_API_URL)")
        return False

    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Worker-Signature": f"sha256={_sign(body)}",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, content=body, headers=headers)
    logger.info("Webhook %s -> %s %s", url, response.status_code, response.text[:200])
    return response.status_code < 400


def _upload_to_cloudinary(filepath: str, transformation: Optional[Any]) -> dict:
    kwargs: dict[str, Any] = {
        "resource_type": "video",
        "folder": "social-bot-videos",
        "overwrite": True,
        "eager_async": True,
    }
    if transformation:
        kwargs["transformation"] = transformation
    return cloudinary.uploader.upload(filepath, **kwargs)


async def _process(job_id: str, job: JobRequest) -> None:
    await _set_job(job_id, status="running", startedAt=_now())
    filepath: Optional[str] = None
    try:
        result = await asyncio.to_thread(download_video, job.videoUrl)
        filepath = result["filepath"]
        await _set_job(job_id, status="uploading", title=result.get("title"))

        upload = await asyncio.to_thread(_upload_to_cloudinary, filepath, job.transformation)

        payload = {
            "userId": job.userId,
            "cloudinaryUrl": upload["secure_url"],
            "cloudinaryId": upload.get("public_id") or upload["asset_id"],
            "title": job.title or result.get("title") or "Untitled video",
            "platform": job.platform,
            "externalId": job.externalId,
            "uploadDestinations": job.uploadDestinations,
        }
        delivered = await _notify(payload, job.callbackUrl)
        await _set_job(
            job_id,
            status="done" if delivered else "delivery_failed",
            finishedAt=_now(),
            videoUrl=upload["secure_url"],
        )
    except CookiesRequiredError as exc:
        await _set_job(job_id, status="failed", reason="cookies_required", error=str(exc))
        logger.error("[%s] %s", job_id, exc)
    except JSRuntimeMissingError as exc:
        await _set_job(job_id, status="failed", reason="js_runtime_missing", error=str(exc))
        logger.error("[%s] %s", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        await _set_job(job_id, status="failed", error=str(exc)[:500], finishedAt=_now())
        logger.exception("[%s] job failed", job_id)
    finally:
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass


@app.get("/health")
async def health():
    """Readiness probe: shows whether the worker can actually reach YouTube etc."""
    return {
        "status": "ok",
        "deno": shutil.which("deno") is not None,
        "jsRuntimes": js_runtime_status(),
        "ffmpeg": bool(ffmpeg_path()),
        "cookies": {platform: bool(cookie_file_for(platform)) for platform in COOKIE_ENV},
        "callback": f"{APP_API_URL}{CALLBACK_PATH}",
        "jobs": len(JOBS),
    }


@app.post("/jobs", status_code=202)
async def create_job(job: JobRequest, x_worker_token: Optional[str] = Header(default=None)):
    if not WORKER_TOKEN or not x_worker_token or not hmac.compare_digest(
        WORKER_TOKEN, x_worker_token
    ):
        raise HTTPException(status_code=401, detail="Invalid worker token")

    job_id = uuid.uuid4().hex
    await _set_job(
        job_id,
        status="queued",
        url=job.videoUrl,
        userId=job.userId,
        createdAt=_now(),
    )
    asyncio.create_task(_process(job_id, job))
    logger.info("Queued job %s for user %s url=%s", job_id, job.userId, job.videoUrl)
    return {"jobId": job_id, "status": "queued"}


@app.get("/jobs")
async def list_jobs():
    return {"jobs": JOBS}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job


if __name__ == "__main__":
    # Lets `python worker_service.py` start the service directly. In production use
    # `uvicorn worker_service:app --host 0.0.0.0 --port $PORT` (see README).
    import uvicorn

    if not WORKER_TOKEN:
        logger.warning("WORKER_TOKEN is empty - every POST /jobs will be rejected")
    if not INTERNAL_SECRET:
        logger.warning("INTERNAL_SECRET is empty - the app will reject every callback")
    if not APP_API_URL:
        logger.warning("APP_API_URL is empty - finished jobs cannot be delivered")
    uvicorn.run(app, host=os.getenv("WORKER_HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080") or 8080))