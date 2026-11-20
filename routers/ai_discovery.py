"""
ai_discovery_routes.py
───────────────────────
FastAPI routes for the AI Discovery feature.
Uses a directly-imported `prisma` client instance (from db import prisma)
instead of a per-request get_db() dependency.

  Dashboard-facing (session auth — user is logged into the web UI):
    GET   /dashboard/ai-discovery                  → renders the page
    GET   /dashboard/api/ai-discovery/config        → fetch current config
    PUT   /dashboard/api/ai-discovery/config        → update config (toggle, schedule, profiles)
    POST  /dashboard/api/ai-discovery/run-now        → trigger an immediate run

  Bot-facing (UserApiKey bearer auth — called by main.py from GitHub Actions):
    GET   /dashboard/api/user/bot-config             → bot fetches its search config
    POST  /dashboard/api/videos/ingest-discovered     → bot posts discovered videos
    PUT   /dashboard/api/user/run-status              → bot reports run completion

  Admin-facing (MASTER_API_KEY — called by the GH Actions dispatcher):
    GET   /dashboard/api/admin/due-users              → who needs to run right now

Adjust `get_current_user` and `compute_next_run` imports to match your app.
"""

import os
import json
import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Header,BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from controller.url_video_controller import FromPlatform, user_transformations
from controller.worker_client import (
    dispatch_download,
    last_dispatch_error,
    running_on_vercel,
    worker_enabled,
)
from models.video import (
    QuotaExceededError,
    assert_can_schedule,
    create_queued_video,
)
from prisma.models import User
from controller.mail_controller import new_video_scheduled_mail
from utils import queued_title

# ── Direct prisma import — no per-request get_db dependency ────────────────────
from lib.depends import get_current_user
from db import prisma   # session-auth dependency
from lib.cron_utils import compute_next_run               # see helper at bottom
    # see cron_utils.py
router = APIRouter()
templates = Jinja2Templates(directory="templates")

MASTER_API_KEY = os.getenv("MASTER_API_KEY", "")
import logging

logger = logging.getLogger("ai-discovery")

# Max discovered videos actually queued per ingest call. The bot may send a
# full batch; anything beyond this is skipped to protect Cloudinary quota.
MAX_DISCOVERED_PER_RUN = int(os.getenv("MAX_DISCOVERED_PER_RUN", "2"))

# Optional: lets "Run now" trigger the GitHub Actions workflow immediately via
# repository_dispatch. Without it, runs wait for the hourly dispatcher poll.
GITHUB_DISPATCH_REPO  = os.getenv("GITHUB_DISPATCH_REPO", "")    # "owner/repo"
GITHUB_DISPATCH_TOKEN = os.getenv("GITHUB_DISPATCH_TOKEN", "")


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

def hash_api_key(raw_key: str) -> str:
    """Match however UserApiKey.apiKey is hashed elsewhere in your app."""
    return hashlib.sha256(raw_key.strip().encode()).hexdigest()


async def get_user_from_bearer(authorization: str = Header(...)) -> User:
    """
    Resolves a Bearer token (raw USER_API_KEY) → User.
    Used by bot-facing endpoints called from main.py / GitHub Actions.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    raw_key = authorization.removeprefix("Bearer ").strip()


    # Also print the saved hash from DB for direct comparison
    key_row = await prisma.userapikey.find_unique(
        where={"apiKey": hashlib.sha256(raw_key.encode()).hexdigest()},
        include={"user": True},
    )

    if not key_row or not key_row.isActive:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    if key_row.expiresAt and key_row.expiresAt < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="API key expired")

    await prisma.userapikey.update(
        where={"id": key_row.id},
        data={"lastUsedAt": datetime.now(timezone.utc)},
    )

    return key_row.user


def require_master_key(authorization: str = Header(...)):
    """Guards admin-only endpoints with MASTER_API_KEY."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not MASTER_API_KEY or token != MASTER_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid master key")


def _as_utc(value):
    """Normalise a DB datetime to timezone-aware UTC.

    Prisma returns naive datetimes for `DateTime`, so comparing them directly
    with `datetime.now(timezone.utc)` raises TypeError.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class SearchProfile(BaseModel):
    query: str
    type: str = "keyword"


class AiDiscoveryConfigUpdate(BaseModel):
    isActive: Optional[bool] = None
    searchProfiles: Optional[list[SearchProfile]] = None
    enabledPlatforms: Optional[list[str]] = None   # ["Youtube","Tiktok","Instagram","Facebook"]
    videoFormat: Optional[str] = None            # shorts | long
    minDurationSec: Optional[int] = Field(None, ge=15, le=3600)
    maxDurationSec: Optional[int] = Field(None, ge=15, le=3600)
    minScore: Optional[float] = Field(None, ge=0.0, le=1.0)
    batchSize: Optional[int] = Field(None, ge=1, le=50)
    maxPerPlatform: Optional[int] = Field(None, ge=1, le=50)
    timeFilter: Optional[str] = None     # day | week | month | year
    cronExpr: Optional[str] = None
    timezone: Optional[str] = None


class RunStatusUpdate(BaseModel):
    status: str                  # success | failed
    videoCount: int = 0
    topVideoUrl: Optional[str] = None
    viralVideoUrl: Optional[str] = None
    error: Optional[str] = None


class IngestVideo(BaseModel):
    externalId: str = ""
    platform: str = "unknown"
    title: str
    description: str = ""
    videoUrl: str
    score: float = 0.0
    # yt-dlp length in seconds. None when the platform is auth-walled
    # (Instagram/Facebook), in which case the server cannot range-check it.
    durationSec: Optional[int] = None


class IngestPayload(BaseModel):
    keyword: str
    videos: list[IngestVideo]


CRON_PRESETS = {
    "every_3h":  "0 */3 * * *",
    "every_6h":  "0 */6 * * *",
    "every_12h": "0 */12 * * *",
    "daily":     "0 9 * * *",
    "twice_daily": "0 9,21 * * *",
    "weekly":    "0 9 * * 1",
}

DEFAULT_PROFILES = [{"query": "trending shorts today", "type": "keyword"}]
DEFAULT_PLATFORMS = ["Youtube", "Tiktok", "Instagram", "Facebook"]

# Presets mirror the dashboard UI: Shorts 1-3 min, Long-form 4-7 min.
# Users can widen the window, but it must stay inside DURATION_SEC_BOUNDS.
VIDEO_FORMAT_PRESETS = {"shorts": (60, 180), "long": (240, 420)}
DURATION_SEC_BOUNDS = (15, 3600)


def _parse_profiles(raw) -> list:
    """Normalise searchProfiles for templates/APIs.

    Rows written before the list type was introduced hold a JSON string
    ("[{...}]") in the Json column, so iterating them directly rendered one
    empty keyword row per character.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict) and p.get("query")]


def _validate_duration_window(min_sec: int, max_sec: int) -> None:
    low, high = DURATION_SEC_BOUNDS
    if min_sec < low or max_sec > high:
        raise HTTPException(
            status_code=400,
            detail=f"Video duration must stay between {low} and {high} seconds",
        )
    if min_sec >= max_sec:
        raise HTTPException(400, "minDurationSec must be lower than maxDurationSec")


def _config_duration_window(config):
    """(min_seconds, max_seconds) from a config row, or None when unset.

    getattr keeps older generated Prisma clients (before the duration
    columns existed) from raising AttributeError during a rolling deploy.
    """
    if config is None:
        return None
    min_sec = getattr(config, "minDurationSec", None)
    max_sec = getattr(config, "maxDurationSec", None)
    if min_sec is None or max_sec is None:
        return None
    min_sec, max_sec = int(min_sec), int(max_sec)
    if min_sec <= 0 or max_sec <= min_sec:
        return None
    return min_sec, max_sec


async def get_or_create_config(user_id: int, create_if_missing: bool = False):
    """Returns the raw Prisma AiDiscoveryConfig row, creating defaults if missing."""
    config = await prisma.aidiscoveryconfig.find_unique(where={"userId": user_id})

    if config:
        return config
    elif create_if_missing:
        new_config = await prisma.aidiscoveryconfig.create(
            data={
                "userId": user_id,
                "isActive": False,
                "searchProfiles": json.dumps(DEFAULT_PROFILES),
                "enabledPlatforms": DEFAULT_PLATFORMS,
                "videoFormat": "shorts",
                "minDurationSec": 60,
                "maxDurationSec": 180,
                "minScore": 0.5,
                "batchSize": 10,
                "maxPerPlatform": 5,
                "timeFilter": "week",
                "cronExpr": CRON_PRESETS["every_6h"],
                "timezone": "UTC",
            }
        )
        return new_config
    return None


def serialize_config(config) -> dict:
    """Converts a Prisma AiDiscoveryConfig row into a plain JSON-safe dict."""
    return {
        "isActive":             config.isActive,
        "searchProfiles":       _parse_profiles(config.searchProfiles),
        "enabledPlatforms":     config.enabledPlatforms,
        "videoFormat":          getattr(config, "videoFormat", "shorts") or "shorts",
        "minDurationSec":       getattr(config, "minDurationSec", 60) or 60,
        "maxDurationSec":       getattr(config, "maxDurationSec", 180) or 180,
        "minScore":             config.minScore,
        "batchSize":            config.batchSize,
        "maxPerPlatform":       config.maxPerPlatform,
        "timeFilter":           config.timeFilter,
        "cronExpr":             config.cronExpr,
        "timezone":             config.timezone,
        "lastRunAt":            config.lastRunAt,
        "nextRunAt":            config.nextRunAt,
        "lastRunStatus":        config.lastRunStatus,
        "lastRunVideoCount":    config.lastRunVideoCount,
        "lastRunTopVideoUrl":   config.lastRunTopVideoUrl,
        "lastRunViralVideoUrl": config.lastRunViralVideoUrl,
        "lastRunError":         config.lastRunError,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD PAGE
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/ai-discovery", response_class=HTMLResponse)
async def ai_discovery_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    config = await get_or_create_config(user.id)
    if config is None:
        return templates.TemplateResponse("/dashboard/activate_ai_discovery.html",{"request": request,"user_info": user})
    return templates.TemplateResponse(
        "/dashboard/ai_discovery.html",
        {
            "request": request,
            "user_info": user,
            "config": config,
            "search_profiles": _parse_profiles(config.searchProfiles),
            "cron_presets": CRON_PRESETS,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD API — session auth (called by the page's own JS)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/api/ai-discovery/config")
async def get_config(user: User = Depends(get_current_user)):
    config = await get_or_create_config(user.id)
    if config is None:
        raise HTTPException(404, "AI Discovery is not configured yet")
    return serialize_config(config)

@router.post("/dashboard/api/ai-discovery/activate")
async def activate_discovery(user: User = Depends(get_current_user)):
    """First-run setup: create the row, switch it on and schedule it.

    The old handler did `raise RedirectResponse(...)` with the response class
    never imported, so the first "Initialize Discovery Engine" click always
    failed with a NameError.
    """
    config = await get_or_create_config(user.id, create_if_missing=True)
    updated = await prisma.aidiscoveryconfig.update(
        where={"userId": user.id},
        data={
            "isActive": True,
            "nextRunAt": compute_next_run(config.cronExpr, config.timezone),
        },
    )
    return {"success": True, "config": serialize_config(updated)}


@router.put("/dashboard/api/ai-discovery/config")
async def update_config(
    payload: AiDiscoveryConfigUpdate,
    user: User = Depends(get_current_user),
):
    # create_if_missing: saving straight from the API used to fail with a
    # "record not found" error when the row had never been created.
    config = await get_or_create_config(user.id, create_if_missing=True)

    data = {}
    if payload.isActive is not None:
        data["isActive"] = payload.isActive
    if payload.searchProfiles is not None:
        if len(payload.searchProfiles) == 0:
            raise HTTPException(400, "At least one search profile is required")
        data["searchProfiles"] = json.dumps([p.dict() for p in payload.searchProfiles])
    if payload.enabledPlatforms is not None:
        valid = {"Youtube", "Tiktok", "Instagram", "Facebook"}
        if not set(payload.enabledPlatforms).issubset(valid):
            raise HTTPException(400, f"enabledPlatforms must be subset of {valid}")
        if len(payload.enabledPlatforms) == 0:
            raise HTTPException(400, "At least one platform must be enabled")
        data["enabledPlatforms"] = payload.enabledPlatforms
    if payload.videoFormat is not None:
        video_format = payload.videoFormat.strip().lower()
        if video_format not in VIDEO_FORMAT_PRESETS:
            raise HTTPException(400, "videoFormat must be shorts|long")
        data["videoFormat"] = video_format
    if payload.minDurationSec is not None or payload.maxDurationSec is not None:
        min_sec = (
            payload.minDurationSec
            if payload.minDurationSec is not None
            else int(getattr(config, "minDurationSec", 60))
        )
        max_sec = (
            payload.maxDurationSec
            if payload.maxDurationSec is not None
            else int(getattr(config, "maxDurationSec", 180))
        )
        _validate_duration_window(min_sec, max_sec)
        data["minDurationSec"] = min_sec
        data["maxDurationSec"] = max_sec
    if payload.minScore is not None:
        data["minScore"] = payload.minScore
    if payload.batchSize is not None:
        data["batchSize"] = payload.batchSize
    if payload.maxPerPlatform is not None:
        data["maxPerPlatform"] = payload.maxPerPlatform
    if payload.timeFilter is not None:
        if payload.timeFilter not in ("day", "week", "month", "year"):
            raise HTTPException(400, "timeFilter must be day|week|month|year")
        data["timeFilter"] = payload.timeFilter
    if payload.cronExpr is not None:
        data["cronExpr"] = payload.cronExpr
    if payload.timezone is not None:
        data["timezone"] = payload.timezone

    # Recompute nextRunAt if schedule or active-state changed
    if "cronExpr" in data or "isActive" in data:
        is_active = data.get("isActive", config.isActive)
        cron_expr = data.get("cronExpr", config.cronExpr)
        tz = data.get("timezone", config.timezone)
        data["nextRunAt"] = compute_next_run(cron_expr, tz) if is_active else None

    updated = await prisma.aidiscoveryconfig.update(
        where={"userId": user.id},
        data=data,
    )

    return {"success": True, "config": serialize_config(updated)}


@router.post("/dashboard/api/ai-discovery/run-now")
async def run_now(user: User = Depends(get_current_user)):
    """
    Trigger an immediate run.

    When GITHUB_DISPATCH_REPO / GITHUB_DISPATCH_TOKEN are configured this fires
    a repository_dispatch so the workflow starts right away. Otherwise the run
    is queued by setting nextRunAt to now and the hourly dispatcher picks it up.
    """
    config = await get_or_create_config(user.id)

    if config is None:
        raise HTTPException(400, "AI Discovery is not configured yet")
    if not config.isActive:
        raise HTTPException(400, "Enable AI Discovery before running")

    key_row = await prisma.userapikey.find_first(
        where={"userId": user.id, "isActive": True}
    )
    if not key_row or not key_row.raw_token:
        raise HTTPException(400, "No active API key found for this user")

    await prisma.aidiscoveryconfig.update(
        where={"userId": user.id},
        data={"nextRunAt": datetime.now(timezone.utc), "lastRunStatus": "queued"},
    )

    if GITHUB_DISPATCH_REPO and GITHUB_DISPATCH_TOKEN:
        try:
            await asyncio.to_thread(_dispatch_discovery_run, key_row.raw_token)
            return {"success": True, "message": "Run dispatched — starting now"}
        except Exception as e:
            logger.warning(f"repository_dispatch failed, falling back to scheduler: {e}")

    return {"success": True, "message": "Run queued — the hourly dispatcher will pick it up"}


def _dispatch_discovery_run(raw_token: str) -> None:
    """Fire the discovery workflow immediately for a single user."""
    resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_DISPATCH_REPO}/dispatches",
        json={
            "event_type": "discover-videos",
            "client_payload": {"user_api_key": raw_token},
        },
        headers={
            "Authorization": f"Bearer {GITHUB_DISPATCH_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    resp.raise_for_status()


# ─────────────────────────────────────────────────────────────────────────────
# BOT-FACING API — UserApiKey bearer auth (called by main.py)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/api/user/bot-config")
async def bot_get_config(user: User = Depends(get_user_from_bearer)):
    """
    Called by config.py's fetch_user_config(). Returns the shape main.py expects.
    """
    config = await prisma.aidiscoveryconfig.find_unique(where={"userId": user.id})

    if not config or not config.isActive:
        raise HTTPException(status_code=404, detail="AI Discovery not configured/active")

    platform_map = {
        "Youtube":   "youtube",
        "Tiktok":    "tiktok",
        "Instagram": "instagram",
        "Facebook":  "facebook",
    }
    enabled = [platform_map[p] for p in config.enabledPlatforms if p in platform_map]

    return {
        "search_profiles":   _parse_profiles(config.searchProfiles),
        "enabled_platforms": enabled,
        "video_format":      getattr(config, "videoFormat", "shorts") or "shorts",
        "min_duration_sec":  int(getattr(config, "minDurationSec", 60) or 60),
        "max_duration_sec":  int(getattr(config, "maxDurationSec", 180) or 180),
        "min_score":         config.minScore,
        "batch_size":        config.batchSize,
        "time_filter":       config.timeFilter,
        "max_per_platform":  config.maxPerPlatform,
        "request_timeout":   10,
    }


@router.put("/dashboard/api/user/run-status")
async def bot_report_run_status(
    payload: RunStatusUpdate,
    user: User = Depends(get_user_from_bearer),
):
    """
    Called by main.py at the end of run() to report results back.
    Powers the "Next Scheduled" / "Last Run" widgets on the dashboard.
    """
    config = await prisma.aidiscoveryconfig.find_unique(where={"userId": user.id})
    if not config:
        raise HTTPException(404, "Config not found")

    next_run = compute_next_run(config.cronExpr, config.timezone) if config.isActive else None

    await prisma.aidiscoveryconfig.update(
        where={"userId": user.id},
        data={
            "lastRunAt":             datetime.now(timezone.utc),
            "nextRunAt":             next_run,
            "lastRunStatus":         payload.status,
            "lastRunVideoCount":     payload.videoCount,
            "lastRunTopVideoUrl":    payload.topVideoUrl,
            "lastRunViralVideoUrl": payload.viralVideoUrl,
            "lastRunError":          payload.error,
        },
    )
    return {"success": True}

async def _dispatch_discovered_video(
    user_id: int,
    video_url: str,
    platform: str,
    video_id: Optional[str],
    title: Optional[str],
    upload_destinations: list,
    transformation,
) -> bool:
    """Hand one discovered video to the download worker; True when it was queued.

    Same two steps as /dashboard/upload-from-url: reserve a plan slot by
    dispatching, then create the "job:<id>" Pending row the dashboard and the
    completion webhook work from.
    """
    try:
        await assert_can_schedule(user_id)
    except QuotaExceededError as e:
        logger.warning(f"Queue full for user {user_id}, not dispatching: {e}")
        return False

    dispatched = await dispatch_download(
        user_id=user_id,
        video_url=video_url,
        platform=platform,
        title=None,  # keep the worker's extracted title; placeholder below
        external_id=video_id,
        upload_destinations=upload_destinations,
        transformation=transformation,
    )
    if not dispatched or not dispatched.get("jobId"):
        logger.error(
            f"Worker dispatch failed for {video_url}: "
            f"{last_dispatch_error() or 'no job id returned'}"
        )
        return False

    try:
        await create_queued_video(
            user_id=user_id,
            job_id=dispatched["jobId"],
            title=title or queued_title(platform, video_url),
            upload_destinations=upload_destinations,
            platform=platform,
        )
    except QuotaExceededError as e:
        logger.warning(f"Queue full for user {user_id} after dispatch: {e}")
        return False

    return True


async def _process_discovered_videos(
    user_id: int,
    videos_data: list,
    upload_destinations: list,
) -> dict:
    """
    Queue discovered videos through the download worker, one at a time.

    This runs *inside* the ingest request when a worker is configured: on
    serverless hosts a background task is frozen as soon as the response is
    sent, which is how discovered videos used to be silently dropped. Each
    dispatch creates the usual "job:<id>" Pending row, so clips appear in the
    Videos queue immediately. Without a worker the local pipeline still runs as
    a background task on a long-lived host.
    """
    user = await prisma.user.find_unique(where={"id": user_id})
    if not user:
        logger.error(f"Ingest: user {user_id} not found")
        return {"queued": 0, "skipped": 0}

    # Duration gate: the bot filters with yt-dlp metadata, but re-check here so
    # an out-of-range clip cannot be queued even by an older bot build.
    discovery_config = await prisma.aidiscoveryconfig.find_unique(
        where={"userId": user_id}
    )
    duration_window = _config_duration_window(discovery_config)

    use_worker = worker_enabled()
    # Playground defaults are baked in once per run instead of per video.
    transformation = await user_transformations(user_id) if use_worker else None

    uploaded_videos = []
    seen_ids = set()
    skipped = 0
    try:
        for video_data in videos_data:
            if len(uploaded_videos) >= MAX_DISCOVERED_PER_RUN:
                logger.info(
                    f"Reached per-run limit ({MAX_DISCOVERED_PER_RUN}), "
                    f"skipping the remaining {len(videos_data) - len(uploaded_videos)} videos"
                )
                break

            video_url = video_data.get("videoUrl")
            video_id = video_data.get("externalId")
            platform = (video_data.get("platform") or "").lower()

            if not video_url:
                logger.warning("Skipping discovered video without a URL")
                skipped += 1
                continue

            if platform not in ("tiktok", "facebook", "youtube", "instagram"):
                logger.warning(f"Unsupported platform '{platform}', skipping {video_url}")
                skipped += 1
                continue

            duration = video_data.get("durationSec")
            if duration_window and duration is not None:
                if not (duration_window[0] <= duration <= duration_window[1]):
                    logger.info(
                        f"Duration {duration}s outside "
                        f"{duration_window[0]}-{duration_window[1]}s, "
                        f"skipping {video_url}"
                    )
                    skipped += 1
                    continue

            # Dedupe per user: the same clip may legitimately be discovered by
            # several users, so a global externalId check would drop good videos.
            if video_id:
                if video_id in seen_ids:
                    logger.info(f"Duplicate video inside this batch, skipping: {video_url}")
                    skipped += 1
                    continue
                is_duplicated = await prisma.video.find_first(
                    where={"userId": user_id, "externalId": video_id}
                )
                if is_duplicated:
                    logger.info(f"Duplicate video for user {user_id}, skipping: {video_url}")
                    skipped += 1
                    continue

            logger.info(f"🔄 Processing: {video_data.get('title')} from {platform}")

            if use_worker:
                queued = await _dispatch_discovered_video(
                    user_id=user_id,
                    video_url=video_url,
                    platform=platform,
                    video_id=video_id,
                    title=video_data.get("title"),
                    upload_destinations=upload_destinations,
                    transformation=transformation,
                )
            else:
                # Local fallback: only reaches here on a long-lived host.
                try:
                    source = FromPlatform(
                        video_url=video_url,
                        user_id=user_id,
                        upload_targets=upload_destinations,
                        video_id=video_id,
                    )
                    result = await source[platform]
                except Exception as e:
                    logger.error(f"Error processing {video_url}: {e}")
                    result = {"Error": str(e)}
                queued = "success" in result
                if not queued:
                    logger.error(f"Failed to ingest {video_url}: {result.get('Error')}")

            if not queued:
                skipped += 1
                continue

            if video_id:
                seen_ids.add(video_id)
            uploaded_videos.append({
                "url": video_url,
                "title": video_data.get("title"),
                "score": video_data.get("score"),
                "platform": video_data.get("platform"),
                "externalId": video_data.get("externalId"),
            })

    except Exception as e:
        logger.error(f"Fatal error in video processing: {e}")

    if uploaded_videos:
        # SMTP is blocking; keep it off the event loop.
        await asyncio.to_thread(
            new_video_scheduled_mail,
            recipient=user.email,
            videos=uploaded_videos,
            user_name=user.fullname,
            default_upload_dest=upload_destinations,
        )
        logger.info(
            f"✅ Queued {len(uploaded_videos)} of {len(videos_data)} videos "
            f"for user {user_id} (worker={use_worker})"
        )
    else:
        logger.info(f"ℹ️  No videos queued for user {user_id}")

    return {"queued": len(uploaded_videos), "skipped": skipped}



@router.post("/dashboard/api/videos/ingest-discovered")
async def ingest_discovered_videos(
    payload: IngestPayload,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    """
    Receive discovered videos from the external discovery service.

    Auth: Bearer <user-api-key>

    With a download worker configured the videos are dispatched inside this
    request — serverless hosts freeze background tasks the moment the response
    is sent, which is why discovered videos never reached the worker before.
    The local in-process pipeline (no worker, long-lived host) stays a
    background task.
    """
    try:
        # 1. AUTHORIZATION CHECK
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid authorization")

        raw_token = authorization.removeprefix("Bearer ").strip()
        hashed_token = hashlib.sha256(raw_token.encode()).hexdigest()

        api_key_record = await prisma.userapikey.find_unique(
            where={"apiKey": hashed_token}
        )

        if not api_key_record or not api_key_record.isActive:
            logger.warning("Ingest: invalid API key attempt")
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")

        user_id = api_key_record.userId

        # 2. GET USER DETAILS
        user = await prisma.user.find_unique(
            where={"id": user_id},
            include={"userConfig": True},
        )

        if not user:
            logger.error(f"User {user_id} not found")
            raise HTTPException(status_code=404, detail="User not found")

        # 3. VALIDATE PAYLOAD (Pydantic already guarantees the shape)
        # pydantic 1.10 (pinned in this project) has .dict(); .model_dump() is
        # v2-only and raised AttributeError here before anything was queued.
        videos = [video.dict() for video in payload.videos]
        if not videos:
            return {"success": False, "message": "No videos provided"}

        # 4. RESOLVE UPLOAD DESTINATIONS
        destinations = user.userConfig.defaultUploadDests if user.userConfig else None
        if not destinations:
            destinations = ["Youtube"]
        if not isinstance(destinations, list):
            destinations = [destinations]

        logger.info(
            f"📥 Ingest: {len(videos)} videos for user {user_id} "
            f"(keyword='{payload.keyword}', destinations={destinations})"
        )

        # 5. QUEUE FOR PROCESSING
        if worker_enabled():
            summary = await _process_discovered_videos(
                user_id=user_id,
                videos_data=videos,
                upload_destinations=destinations,
            )
            queued = summary["queued"]
            detail = f"Queued {queued} of {len(videos)} videos on the download worker"
            if not queued:
                detail += " (duplicates, unsupported platform, or queue full)"

            # Update API key last used
            await prisma.userapikey.update(
                where={"apiKey": hashed_token},
                data={"lastUsedAt": datetime.now(timezone.utc)},
            )

            return {
                "success": True,
                "message": detail,
                "queued": queued,
                "skipped": summary["skipped"],
            }

        if running_on_vercel():
            # A background task would be frozen before it ran a single video.
            logger.error(
                "Ingest on Vercel without WORKER_SERVICE_URL / WORKER_TOKEN - "
                "discovered videos cannot be processed"
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Download worker is not configured on this deployment "
                    "(WORKER_SERVICE_URL / WORKER_TOKEN). Discovered videos "
                    "were not queued."
                ),
            )

        background_tasks.add_task(
            _process_discovered_videos,
            user_id=user_id,
            videos_data=videos,
            upload_destinations=destinations,
        )

        # Update API key last used
        await prisma.userapikey.update(
            where={"apiKey": hashed_token},
            data={"lastUsedAt": datetime.now(timezone.utc)},
        )

        return {"success": True, "message": f"Processing {len(videos)} videos"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ingest error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN API — MASTER_API_KEY auth (called by GitHub Actions dispatcher)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/api/admin/due-users")
async def get_due_users(_: None = Depends(require_master_key)):
    """
    Returns users whose nextRunAt is due, then rotates nextRunAt forward so the
    same user is not re-fired on the next hourly poll.

    This is what makes each user's own cronExpr / timezone meaningful.
    """
    now = datetime.now(timezone.utc)
    configs = await prisma.aidiscoveryconfig.find_many(
        where={"isActive": True},
        include={"user": {"include": {"userApiKey": True}}},
    )

    results = []
    for cfg in configs:
        next_run = _as_utc(cfg.nextRunAt)
        if next_run is not None and next_run > now:
            continue

        active_key = next(
            (k for k in cfg.user.userApiKey if k.isActive and k.raw_token), None
        )
        if not active_key:
            continue

        results.append({
            "user_id": str(cfg.userId),
            "api_key": active_key.raw_token,
        })

        await prisma.aidiscoveryconfig.update(
            where={"id": cfg.id},
            data={"nextRunAt": compute_next_run(cfg.cronExpr, cfg.timezone)},
        )

    logger.info(f"Dispatcher: {len(results)} of {len(configs)} active users are due")
    return {"users": results}
