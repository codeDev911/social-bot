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
from controller.url_video_controller import FromPlatform
from prisma.models import User
from controller.mail_controller import new_video_scheduled_mail

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

# Max discovered videos actually downloaded + stored per ingest call. The bot may
# send a full batch; anything beyond this is skipped to protect Cloudinary quota.
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
        "searchProfiles":    config.searchProfiles or "[]",
        "enabledPlatforms":     config.enabledPlatforms,
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
            "search_profiles": config.searchProfiles if config.searchProfiles else [],
            "cron_presets": CRON_PRESETS,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD API — session auth (called by the page's own JS)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard/api/ai-discovery/config")
async def get_config(user: User = Depends(get_current_user)):
    config = await get_or_create_config(user.id)
    return serialize_config(config)

@router.post("/dashboard/api/ai-discovery/activate")
async def get_config(user: User = Depends(get_current_user)):
    config = await get_or_create_config(user.id, create_if_missing=True)
    if config.isActive:
        raise RedirectResponse(url="/dashboard/ai-discovery")
    return serialize_config(config)


@router.put("/dashboard/api/ai-discovery/config")
async def update_config(
    payload: AiDiscoveryConfigUpdate,
    user: User = Depends(get_current_user),
):
    config = await get_or_create_config(user.id)

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
        "search_profiles":   config.searchProfiles or "[]",
        "enabled_platforms": enabled,
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

async def _process_discovered_videos(
    user_id: int,
    videos_data: list,
    upload_destinations: list,
):
    """
    Download and upload discovered videos, one at a time.

    Runs as a FastAPI background task (after the ingest response is sent).
    Each video goes through FromPlatform just like /upload-from-url, so it lands
    in Cloudinary as a Pending video ready for the publish schedule.
    """
    user = await prisma.user.find_unique(where={"id": user_id})
    if not user:
        logger.error(f"Ingest: user {user_id} not found")
        return

    uploaded_videos = []
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
                continue

            if platform not in ("tiktok", "facebook", "youtube", "instagram"):
                logger.warning(f"Unsupported platform '{platform}', skipping {video_url}")
                continue

            # Dedupe per user: the same clip may legitimately be discovered by
            # several users, so a global externalId check would drop good videos.
            if video_id:
                is_duplicated = await prisma.video.find_first(
                    where={"userId": user_id, "externalId": video_id}
                )
                if is_duplicated:
                    logger.info(f"Duplicate video for user {user_id}, skipping: {video_url}")
                    continue

            logger.info(f"🔄 Processing: {video_data.get('title')} from {platform}")

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
                continue

            if "success" not in result:
                logger.error(f"Failed to ingest {video_url}: {result.get('Error')}")
                continue

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
        logger.info(f"✅ Queued {len(uploaded_videos)} of {len(videos_data)} videos for user {user_id}")
    else:
        logger.info(f"ℹ️  No videos queued for user {user_id}")



@router.post("/dashboard/api/videos/ingest-discovered")
async def ingest_discovered_videos(
    payload: IngestPayload,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    """
    Receive discovered videos from the external discovery service.

    Auth: Bearer <user-api-key>
    Downloads & uploads using FromPlatform (same as upload-from-url).
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
        videos = [video.model_dump() for video in payload.videos]
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
