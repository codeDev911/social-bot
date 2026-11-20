"""Caption + hashtag generation pipeline.

A job describes one video. The web app builds and enqueues it as soon as a
video row exists; ``wroker.py`` runs it from Redis and writes the result back
to Postgres. When Redis is not configured the same job runs in-process right
after the upload, so local development needs no queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from controller.mvp_agent.caption_gen import generate_captions
from controller.mvp_agent.hashtag_gen import generate_hashtags
from db import prisma
from lib import ai_queue
from lib.trending import trending_terms

logger = logging.getLogger(__name__)

# Caption length and hashtag count per destination. The most permissive target
# wins when a video is published to several platforms.
CAPTION_LIMITS = {"tiktok": 300, "instagram": 300, "youtube": 650, "facebook": 500}
HASHTAG_LIMITS = {"tiktok": 8, "instagram": 8, "youtube": 5, "facebook": 4}
DEFAULT_TONE = (os.getenv("AI_DEFAULT_TONE") or "engaging").strip()
CAPTION_MAX_CHARS = 1500
HTAGS_MAX_CHARS = 500

_EXT_RE = re.compile(r"\.(mp4|mov|webm|mkv|avi|m4v)$", re.IGNORECASE)
_INLINE_TASKS: "set[asyncio.Task]" = set()


class PermanentJobError(RuntimeError):
    """The job can never succeed (for example the video was deleted)."""


def clean_title(raw: Any) -> str:
    """Turn a file name or worker title into a prompt-friendly topic."""
    text = str(raw or "").strip().strip("\"'")
    text = _EXT_RE.sub("", text)
    text = re.sub(r"[_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def _platform_key(value: Any) -> str:
    key = str(getattr(value, "value", value) or "").strip().lower()
    return key if key in CAPTION_LIMITS else ""


def _platform_of(video) -> str:
    for target in getattr(video, "upload_destinations", None) or []:
        key = _platform_key(target)
        if key:
            return key
    key = _platform_key(getattr(video, "scrapedFrom", ""))
    if key:
        return key
    return "youtube"


def _plan_key(user) -> str:
    plan = getattr(user, "plan", None)
    plan_type = getattr(plan, "type", None)
    return str(getattr(plan_type, "value", plan_type) or "free").lower()


def _config_flag(config, name: str) -> bool:
    if config is None:
        return True
    return bool(getattr(config, name, True))


def build_job(video, user, *, force: bool = False, reason: str = "upload") -> dict:
    """Everything the worker needs, serialized into one Redis message."""
    config = getattr(user, "userConfig", None)
    platform = _platform_of(video)
    return {
        "videoId": int(video.id),
        "userId": int(video.userId),
        "title": clean_title(getattr(video, "title", "")),
        "platform": platform,
        "tone": DEFAULT_TONE,
        "plan": _plan_key(user),
        "wantCaptions": _config_flag(config, "captionGenEnabled"),
        "wantHashtags": _config_flag(config, "hashtagGenEnabled"),
        "defaultCaption": (getattr(config, "defaultCaption", "") or "").strip() if config else "",
        "defaultHtags": (getattr(config, "defaultHtags", "") or "").strip() if config else "",
        "force": bool(force),
        "reason": reason,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


async def schedule_video_ai(
    video,
    *,
    force: bool = False,
    reason: str = "upload",
    await_inline: bool = False,
) -> dict:
    """Queue AI copy for a video. Never raises: uploads must not fail on AI."""
    try:
        user = await prisma.user.find_unique(
            where={"id": video.userId},
            include={"plan": True, "userConfig": True},
        )
        if user is None:
            return {"skipped": "user-missing"}

        job = build_job(video, user, force=force, reason=reason)
        captions = (getattr(video, "captions", "") or "").strip()
        htags = (getattr(video, "htags", "") or "").strip()

        if not job["wantCaptions"] and not job["wantHashtags"]:
            # Generation is off: fall back to the saved defaults, no queue hop.
            defaults = {}
            if not captions and job["defaultCaption"]:
                defaults["captions"] = job["defaultCaption"][:CAPTION_MAX_CHARS]
            if not htags and job["defaultHtags"]:
                defaults["htags"] = job["defaultHtags"][:HTAGS_MAX_CHARS]
            if defaults:
                await prisma.video.update(where={"id": video.id}, data=defaults)
                return {"applied": "defaults"}
            return {"skipped": "generation-disabled"}

        if not force and captions and htags:
            return {"skipped": "already-generated"}

        try:
            job_id = await ai_queue.enqueue(job)
            logger.info("Queued AI job %s for video %s (%s)", job_id, video.id, reason)
            return {"queued": True, "jobId": job_id}
        except ai_queue.AlreadyQueued:
            return {"queued": False, "skipped": "already-queued"}
        except ai_queue.QueueUnavailable as exc:
            logger.warning("AI queue unavailable (%s); generating inline for video %s", exc, video.id)
            if await_inline:
                result = await run_ai_job(job)
                return {"queued": False, "inline": True, **result}
            _spawn_inline(job)
            return {"queued": False, "inline": True}
    except Exception as exc:  # noqa: BLE001 - never break the upload path
        logger.exception("Could not schedule AI copy for video %s", getattr(video, "id", "?"))
        return {"error": str(exc)}


def _spawn_inline(job: dict) -> None:
    task = asyncio.create_task(_run_inline(job))
    _INLINE_TASKS.add(task)
    task.add_done_callback(_INLINE_TASKS.discard)


async def _run_inline(job: dict) -> None:
    try:
        await run_ai_job(job)
    except Exception:  # noqa: BLE001
        logger.exception("Inline AI generation failed for video %s", job.get("videoId"))


async def run_ai_job(job: dict) -> dict:
    """Generate whatever is missing for one video and persist it."""
    video_id = int(job["videoId"])
    user_id = int(job["userId"])

    await ai_queue.set_status(video_id, status="processing", phase="starting", error="")
    video = await prisma.video.find_unique(where={"id": video_id})
    if video is None:
        raise PermanentJobError(f"video {video_id} no longer exists")

    topic = clean_title(job.get("title") or getattr(video, "title", ""))
    if not topic:
        topic = "this video"
    platform = (job.get("platform") or _platform_of(video)).lower()
    tone = job.get("tone") or DEFAULT_TONE
    caption_max = CAPTION_LIMITS.get(platform, 650)
    hashtag_max = HASHTAG_LIMITS.get(platform, 5)
    force = bool(job.get("force"))
    want_captions = bool(job.get("wantCaptions"))
    want_htags = bool(job.get("wantHashtags"))

    captions_text = (video.captions or "").strip()
    htags_text = (video.htags or "").strip()
    if force:
        if want_captions:
            captions_text = ""
        if want_htags:
            htags_text = ""

    errors: list[str] = []
    caption_generated = False
    hashtag_generated = False

    if want_captions and not captions_text:
        try:
            options = await asyncio.to_thread(
                generate_captions, topic, tone, {"caption_max": caption_max}
            )
            best = next((str(option).strip() for option in options or [] if str(option).strip()), "")
            if not best:
                raise RuntimeError("caption model returned nothing")
            captions_text = best[:CAPTION_MAX_CHARS]
            caption_generated = True
            await ai_queue.set_status(video_id, status="processing", phase="hashtags")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"caption: {exc}")
            logger.warning("Caption generation failed for video %s: %s", video_id, exc)

    if want_htags and not htags_text:
        try:
            trending = await asyncio.to_thread(trending_terms, topic, platform, 10)
            tags = await asyncio.to_thread(
                generate_hashtags,
                topic,
                tone,
                {"hashtags_max": hashtag_max, "trending": trending},
            )
            unique = [tag for tag in dict.fromkeys(tags or []) if tag]
            if not unique:
                raise RuntimeError("hashtag model returned nothing")
            htags_text = " ".join(unique)[:HTAGS_MAX_CHARS]
            hashtag_generated = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"hashtags: {exc}")
            logger.warning("Hashtag generation failed for video %s: %s", video_id, exc)

    # Saved defaults fill whatever the model could not produce.
    if not captions_text and job.get("defaultCaption"):
        captions_text = job["defaultCaption"][:CAPTION_MAX_CHARS]
    if not htags_text and job.get("defaultHtags"):
        htags_text = job["defaultHtags"][:HTAGS_MAX_CHARS]

    if not captions_text and not htags_text and errors:
        raise RuntimeError("; ".join(errors)[:500])

    update_data = {}
    if captions_text and captions_text != (video.captions or "").strip():
        update_data["captions"] = captions_text
    if htags_text and htags_text != (video.htags or "").strip():
        update_data["htags"] = htags_text
    if update_data:
        await prisma.video.update(where={"id": video_id}, data=update_data)

    if caption_generated or hashtag_generated:
        increments = {}
        if caption_generated:
            increments["captionRequests"] = {"increment": 1}
        if hashtag_generated:
            increments["hTagRequests"] = {"increment": 1}
        try:
            await prisma.usage.update(where={"userId": user_id}, data=increments)
        except Exception:  # noqa: BLE001 - a missing Usage row must not fail the job
            logger.debug("Could not update AI usage for user %s", user_id)

    await ai_queue.set_status(
        video_id,
        status="done",
        phase="done",
        captionChars=len(captions_text),
        hashtagChars=len(htags_text),
        error="; ".join(errors)[:300],
        finishedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    await ai_queue.release(video_id, user_id)

    logger.info(
        "Video %s copy ready (caption=%s chars, tags=%s chars, errors=%s)",
        video_id,
        len(captions_text),
        len(htags_text),
        len(errors),
    )
    return {"captions": captions_text, "htags": htags_text, "errors": errors}