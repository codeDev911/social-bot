"""Redis-backed queue for caption / hashtag jobs.

One job is enqueued per created video by the web app and consumed by
``wroker.py``. Status hashes are written back to Redis on every transition,
which is what the dashboard reads for "Processing Queue" and caption badges.

Without ``REDIS_URL`` every call reports "disabled" and the caller generates
inline, so local development never depends on Redis.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from lib import redis_client

logger = logging.getLogger(__name__)

QUEUE_KEY = (os.getenv("AI_QUEUE_KEY") or "socialbot:ai:queue").strip()
PROCESSING_KEY = f"{QUEUE_KEY}:processing"
RETRY_KEY = f"{QUEUE_KEY}:retry"
DEAD_KEY = f"{QUEUE_KEY}:dead"
HEARTBEAT_KEY = f"{QUEUE_KEY}:heartbeat"
STATUS_PREFIX = f"{QUEUE_KEY}:status:"
USER_PREFIX = f"{QUEUE_KEY}:user:"
LOCK_PREFIX = f"{QUEUE_KEY}:lock:"

STATUS_TTL = int(os.getenv("AI_STATUS_TTL") or 7 * 24 * 3600)
LOCK_TTL = int(os.getenv("AI_JOB_LOCK_TTL") or 900)
DEAD_MAX = 100

# Atomic retry promotion: due members move from the retry ZSET onto the queue.
_PROMOTE_SCRIPT = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 20)
for _, member in ipairs(due) do
  redis.call('ZREM', KEYS[1], member)
  redis.call('LPUSH', KEYS[2], member)
end
return #due
"""


class QueueUnavailable(RuntimeError):
    """Redis is not configured or unreachable; the caller should run inline."""


class AlreadyQueued(RuntimeError):
    """A job for this video is already waiting or running."""


def queue_enabled() -> bool:
    return redis_client.redis_enabled()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _status_key(video_id: Any) -> str:
    return f"{STATUS_PREFIX}{int(video_id)}"


def _user_key(user_id: Any) -> str:
    return f"{USER_PREFIX}{int(user_id)}"


def _lock_key(video_id: Any) -> str:
    return f"{LOCK_PREFIX}{int(video_id)}"


async def enqueue(job: dict) -> str:
    """Push one job and mark the video as queued. Returns the job id."""
    client = await redis_client.get_client()
    if client is None:
        raise QueueUnavailable("REDIS_URL is not configured")

    video_id = int(job["videoId"])
    user_id = int(job["userId"])
    job_id = job.get("jobId") or uuid.uuid4().hex
    job["jobId"] = job_id
    job.setdefault("enqueuedAt", _now())
    raw = json.dumps(job, separators=(",", ":"), default=str)

    try:
        acquired = await client.set(_lock_key(video_id), job_id, nx=True, ex=LOCK_TTL)
        if not acquired:
            raise AlreadyQueued(f"video {video_id} already has an AI job")

        pipe = client.pipeline()
        pipe.lpush(QUEUE_KEY, raw)
        pipe.sadd(_user_key(user_id), str(video_id))
        pipe.expire(_user_key(user_id), STATUS_TTL)
        pipe.hset(
            _status_key(video_id),
            mapping={
                "videoId": str(video_id),
                "userId": str(user_id),
                "jobId": job_id,
                "status": "queued",
                "phase": "queued",
                "attempts": str(int(job.get("attempts") or 0)),
                "error": "",
                "enqueuedAt": job["enqueuedAt"],
                "updatedAt": _now(),
            },
        )
        pipe.expire(_status_key(video_id), STATUS_TTL)
        await pipe.execute()
        return job_id
    except AlreadyQueued:
        raise
    except Exception as exc:  # noqa: BLE001 - network and auth failures both land here
        logger.warning("Could not enqueue AI job for video %s: %s", video_id, exc)
        raise QueueUnavailable(str(exc)) from exc


async def set_status(video_id: Any, **fields: Any) -> None:
    """Merge fields into the video's status hash (best effort)."""
    client = await redis_client.get_client()
    if client is None:
        return
    data = {key: ("" if value is None else str(value)) for key, value in fields.items()}
    data["updatedAt"] = _now()
    try:
        pipe = client.pipeline()
        pipe.hset(_status_key(video_id), mapping=data)
        pipe.expire(_status_key(video_id), STATUS_TTL)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not write status for video %s: %s", video_id, exc)


async def get_status(video_id: Any) -> Optional[dict]:
    client = await redis_client.get_client()
    if client is None:
        return None
    try:
        data = await client.hgetall(_status_key(video_id))
        return data or None
    except Exception:  # noqa: BLE001
        return None


async def active_statuses(user_id: Any) -> list[dict]:
    """Status hashes for jobs that are still queued or running for a user."""
    client = await redis_client.get_client()
    if client is None:
        return []

    active: list[dict] = []
    try:
        members = await client.smembers(_user_key(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read active AI jobs for user %s: %s", user_id, exc)
        return []

    for member in members:
        status = await get_status(member)
        if not status:
            # Expired or never written: drop the stale index entry.
            try:
                await client.srem(_user_key(user_id), member)
            except Exception:
                pass
            continue
        state = (status.get("status") or "").lower()
        if state in {"done", "failed"}:
            try:
                await client.srem(_user_key(user_id), member)
            except Exception:
                pass
            continue
        try:
            status["videoId"] = int(member)
        except (TypeError, ValueError):
            status["videoId"] = member
        active.append(status)
    return active


async def release(video_id: Any, user_id: Any) -> None:
    """Terminal cleanup: forget the index entry and free the duplicate lock."""
    client = await redis_client.get_client()
    if client is None:
        return
    try:
        pipe = client.pipeline()
        pipe.srem(_user_key(user_id), str(int(video_id)))
        pipe.delete(_lock_key(video_id))
        await pipe.execute()
    except Exception:  # noqa: BLE001
        pass


async def snapshot() -> dict:
    """Queue depths plus the worker heartbeat, for the dashboard."""
    if not queue_enabled():
        return {
            "enabled": False,
            "online": False,
            "queued": 0,
            "processing": 0,
            "dead": 0,
            "heartbeat": None,
        }

    client = await redis_client.get_client()
    if client is None:
        return {"enabled": False, "online": False, "queued": 0, "processing": 0, "dead": 0, "heartbeat": None}

    try:
        pipe = client.pipeline()
        pipe.llen(QUEUE_KEY)
        pipe.llen(PROCESSING_KEY)
        pipe.llen(DEAD_KEY)
        pipe.get(HEARTBEAT_KEY)
        queued, processing, dead, heartbeat = await pipe.execute()
        return {
            "enabled": True,
            "online": bool(heartbeat),
            "queued": int(queued or 0),
            "processing": int(processing or 0),
            "dead": int(dead or 0),
            "heartbeat": heartbeat,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read AI queue snapshot: %s", exc)
        return {"enabled": True, "online": False, "queued": 0, "processing": 0, "dead": 0, "heartbeat": None}


async def heartbeat(client=None) -> None:
    client = client or await redis_client.get_client()
    if client is None:
        return
    try:
        await client.set(HEARTBEAT_KEY, _now(), ex=90)
    except Exception:  # noqa: BLE001
        pass


async def promote_retries(client) -> int:
    """Move retries whose backoff has elapsed back onto the queue."""
    try:
        return int(await client.eval(_PROMOTE_SCRIPT, 2, RETRY_KEY, QUEUE_KEY, time.time()) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not promote retries: %s", exc)
        return 0


async def schedule_retry(client, job: dict, delay_seconds: int) -> None:
    raw = json.dumps(job, separators=(",", ":"), default=str)
    try:
        await client.zadd(RETRY_KEY, {raw: time.time() + max(1, int(delay_seconds))})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not schedule retry for video %s: %s", job.get("videoId"), exc)


async def dead_letter(client, job: dict) -> None:
    raw = json.dumps(job, separators=(",", ":"), default=str)
    try:
        pipe = client.pipeline()
        pipe.lpush(DEAD_KEY, raw)
        pipe.ltrim(DEAD_KEY, 0, DEAD_MAX - 1)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not dead-letter video %s: %s", job.get("videoId"), exc)


async def recover_inflight(client) -> int:
    """Put jobs back on the queue after an unclean shutdown.

    Run one AI worker replica per queue: two workers recovering at the same
    time would duplicate in-flight jobs.
    """
    try:
        raw_items = await client.lrange(PROCESSING_KEY, 0, -1)
    except Exception:  # noqa: BLE001
        return 0
    recovered = 0
    for raw in raw_items:
        try:
            await client.lpush(QUEUE_KEY, raw)
            await client.lrem(PROCESSING_KEY, 1, raw)
            recovered += 1
        except Exception:  # noqa: BLE001
            continue
    return recovered


async def backfill_candidates(limit: int = 25) -> int:
    """Queue existing videos that still have no AI copy (used by --backfill)."""
    from db import prisma
    from lib.ai_pipeline import schedule_video_ai

    videos = await prisma.video.find_many(
        where={"upload_status": "Pending"},
        order={"created_at": "desc"},
        take=limit,
    )
    queued = 0
    for video in videos:
        if not getattr(video, "cloudinary_url", ""):
            continue
        if (video.captions or "").strip() and (video.htags or "").strip():
            continue
        result = await schedule_video_ai(video, reason="backfill")
        if result.get("queued"):
            queued += 1
    return queued