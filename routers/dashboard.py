from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, Response, UploadFile,Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from controller.pexels import UploadPexelVideo
from controller.url_video_controller import FromPlatform, user_transformations
from controller.worker_client import (
    dispatch_download,
    fetch_job_status,
    fetch_worker_health,
    last_dispatch_error,
    running_on_vercel,
    worker_enabled,
    worker_required,
    worker_status,
)
from controller.video_uploader import  UploadVideo
from controller.cloudinary_transformer import (
    CloudinaryVideoTransformer,
    CROP_GRAVITY,
    DEFAULT_PLAYGROUND_CONFIG,
    TEXT_GRAVITY,
    build_playground_transformation,
    build_playground_transformation_chain,
    build_playground_preview_url,
)
from controller.copyright_transformer import normalize_color
from models.video import (
    QUEUED_PREFIX,
    QuotaExceededError,
    assert_can_schedule,
    complete_queued_video,
    create_queued_video,
    create_video,
    fail_queued_video,
    find_queued_video,
    queued_job_id,
)
from db import prisma
from lib import ai_queue
from lib.ai_pipeline import schedule_video_ai
from lib.depends import get_current_user
from lib.plans import clipper_config, plan_limits
from fastapi.exceptions import HTTPException
import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from utils import validate_video_url,verify_upload_target, queued_title
from controller.delete_uploaded_files import delete_uploaded_files
from controller.buffer import get_profiles_graphql
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from controller.mail_controller import new_video_scheduled_mail
import asyncio
import os
import json
import secrets
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging

load_dotenv()

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/dashboard")

INTERNAL_SECRET=os.getenv("INTERNAL_SECRET")


class BufferConnectRequest(BaseModel):
    bufferApiKey: str

class GenerateApiKeyRequest(BaseModel):
    name: str = "Discovery Service"

class DynamicEditRequest(BaseModel):
    audioControls: dict = {}
    textOverlayControls: dict = {}
    pixelDistortionControls: dict = {}


class PlaygroundConfigRequest(BaseModel):
    """Playground video transformation configuration (UI preview only)"""
    textOverlay: dict = {
        "enabled": False,
        "text": "@mybrand",
        "fontSize": 48,
        "color": "#F97316",
        "opacity": 100,
        "position": "center"
    }
    audioControls: dict = {
        "volume": 0,
        "frequency": "",
        "audioCodec": "aac",
        "bitrate": "128k",
        "removeAudio": False
    }
    resize: dict = {
        "cropMode": "none",
        "width": None,
        "height": None
    }
    trim: dict = {
        "startOffset": 0,
        "duration": 30
    }
    gravity: dict = {
        "gravity": "auto"
    }
    quality: dict = {
        "quality": "auto"
    }


class PlaygroundTransformationRequest(BaseModel):
    """Playground video transformation configuration"""
    video_url: str
    overlay_type: str = "none"
    overlay_text: str = ""
    font_size: int = 36
    text_color: str = "#F97316"
    text_opacity: int = 100
    crop_mode: str = "none"
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = ""
    start_offset: Optional[float] = None
    end_offset: Optional[float] = None
    duration: Optional[float] = None
    gravity: str = ""
    x_offset: int = 0
    y_offset: int = 0
    auto_crop_type: str = "none"
    quality: str = ""
    dpr: str = ""


class UpdateVideoRequest(BaseModel):
    """Request model for updating video details"""
    title: Optional[str] = None
    captions: Optional[str] = None
    upload_destinations: Optional[list] = None


def _normalize_credentials(credentials):
    if not credentials:
        return {}
    return credentials if isinstance(credentials, dict) else {}


# Credentials are stored per connection provider ("google" for the Google
# OAuth account, "tiktok"/"youtube"/... for Buffer channels); uploads and the
# publisher speak canonical platform names.
CREDENTIAL_PLATFORM_MAP = {
    "google": "Youtube",
    "youtube": "Youtube",
    "tiktok": "Tiktok",
    "facebook": "Facebook",
    "instagram": "Instagram",
}
UPLOAD_TARGET_ORDER = ("Youtube", "Tiktok", "Facebook", "Instagram")


def _connected_upload_targets(credentials) -> list:
    """Canonical upload destinations that have a connected account, deduped."""
    available = {
        CREDENTIAL_PLATFORM_MAP[str(key).lower()]
        for key in _normalize_credentials(credentials)
        if str(key).lower() in CREDENTIAL_PLATFORM_MAP
    }
    return [target for target in UPLOAD_TARGET_ORDER if target in available]


def _build_dashboard_context(request: Request, current_user, *, videos=None, total_videos_count=0, pending_videos_count=0, canceled_videos_count=0):
    user_info = current_user
    credentials = _normalize_credentials(getattr(user_info, "credentials", None))
    plan = getattr(user_info, "plan", None)
    usage = getattr(user_info, "usage", None)

    plan_name = (getattr(plan, "name", None) or "Free").strip()
    plan_type = getattr(plan, "type", None)
    plan_limits = {
        "starter": {"videos": 8, "accounts": 2, "ai_features": "Basic"},
        "pro": {"videos": 20, "accounts": 4, "ai_features": "Advanced"},
        "free": {"videos": 2, "accounts": 1, "ai_features": "Basic"},
    }
    plan_key = str(plan_type or plan_name).lower()
    limits = plan_limits.get(plan_key, plan_limits["free"])
    today_uploads = getattr(usage, "uploadsToday", 0) if usage else 0
    scheduled_videos = getattr(usage, "scheduledToday", 0) if usage else 0
    caption_generations = getattr(usage, "captionRequests", 0) if usage else 0
    hashtag_generations = getattr(usage, "hTagRequests", 0) if usage else 0
    ai_processing_used = caption_generations + hashtag_generations
    ai_processing_limit = 1000

    def pct(value, limit):
        if not limit:
            return "0"
        return str(round((value / limit) * 100))

    return {
        "request": request,
        "user_info": user_info,
        "user_id": getattr(user_info, "id", None),
        "connected_platforms": credentials,
        "connected_upload_targets": _connected_upload_targets(credentials),
        "title": "Home Page",
        "videos": videos or [],
        "total_videos_count": total_videos_count,
        "pending_videos_count": pending_videos_count,
        "canceled_videos_count": canceled_videos_count,
        "uploaded_videos_count": sum(1 for video in (videos or []) if getattr(video, "upload_status", None) == "Uploaded"),
        "processing_videos_count": sum(1 for video in (videos or []) if getattr(video, "upload_status", None) == "Processing"),
        "today_uploads": today_uploads,
        "scheduled_videos": scheduled_videos,
        "caption_generations": caption_generations,
        "hashtag_generations": hashtag_generations,
        "ai_processing_used": ai_processing_used,
        "ai_processing_limit": ai_processing_limit,
        "caption_ai_usage": caption_generations,
        "hashtag_ai_usage": hashtag_generations,
        "today_uploads_pct": pct(today_uploads, 8),
        "scheduled_videos_pct": pct(scheduled_videos, 20),
        "caption_generations_pct": pct(caption_generations, 100),
        "hashtag_generations_pct": pct(hashtag_generations, 100),
        "ai_processing_pct": pct(ai_processing_used, ai_processing_limit),
        "current_plan": plan_name,
        "plan_price": "$8" if plan_key == "starter" else "$16" if plan_key == "pro" else "$0",
        "plan_video_limit": limits["videos"],
        "plan_account_limit": limits["accounts"],
        "plan_ai_features": limits["ai_features"],
        "next_billing": getattr(plan, "trailExpiredAt", None),
        # Queue + schedule cards. The dashboard home route overwrites these with
        # live values; other pages keep the safe defaults.
        "next_run_at": None,
        "next_run_label": "No schedule set",
        "next_video_title": "No pending video",
        "next_scheduled_video": None,
        "caption_status": "Pending",
        "caption_chip": "chip-dim",
        "hashtag_status": "Pending",
        "hashtag_chip": "chip-dim",
        "queue_items": [],
        "queue_count": 0,
        "queue_hidden": 0,
        "ai_queue_enabled": ai_queue.queue_enabled(),
        "ai_worker_online": False,
        "ai_queue_depth": 0,
        "ai_queue_processing": 0,
    }


# ==========================================
# DASHBOARD QUEUE + SCHEDULE CARDS
# ==========================================

DOWNLOAD_PHASES = {
    "queued": (15, "Waiting on the download worker"),
    "downloading": (45, "Downloading the source video"),
    "uploading": (75, "Uploading to Cloudinary"),
    "done": (90, "Finalising the video"),
    "failed": (100, "Download failed"),
    "delivery_failed": (100, "Worker could not reach the app"),
}


def _video_status(video) -> str:
    return str(getattr(video, "upload_status", "") or "")


def _as_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _user_zone(config):
    name = (getattr(config, "timezone", "") or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _human_when(local_dt: datetime, local_now: datetime) -> str:
    days = (local_dt.date() - local_now.date()).days
    if days == 0:
        prefix = "Today"
    elif days == 1:
        prefix = "Tomorrow"
    else:
        prefix = local_dt.strftime("%b %d")
    zone = local_dt.tzname() or "UTC"
    return f"{prefix}, {local_dt.strftime('%H:%M')} {zone}"


def _next_schedule(config, video):
    """When the next publish run will happen: the user's daily cron first,
    then the earliest pending video slot, otherwise nothing."""
    zone = _user_zone(config)
    now = datetime.now(timezone.utc)

    raw = (getattr(config, "scheduled", "") or "").strip()
    if raw:
        try:
            hour_part, _, minute_part = raw.partition("-")
            hour = int(hour_part)
            minute = int(minute_part or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                local_now = now.astimezone(zone)
                when = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if when <= local_now:
                    when += timedelta(days=1)
                return when.astimezone(timezone.utc), _human_when(when, local_now)
        except (TypeError, ValueError):
            pass

    scheduled_at = _as_utc(getattr(video, "scheduled_at", None)) if video else None
    if scheduled_at:
        local_now = now.astimezone(zone)
        return scheduled_at, _human_when(scheduled_at.astimezone(zone), local_now)

    return None, "No schedule set"


def _generation_state(video, status, config, kind: str):
    """(label, chip class) for a caption or hashtag badge."""
    content_attr = "captions" if kind == "caption" else "htags"
    enabled_attr = "captionGenEnabled" if kind == "caption" else "hashtagGenEnabled"
    default_attr = "defaultCaption" if kind == "caption" else "defaultHtags"

    if video is not None and (getattr(video, content_attr, "") or "").strip():
        return "Ready", "chip-green"

    state = str((status or {}).get("status") or "").lower()
    if state == "processing":
        return "Generating…", "chip-amber"
    if state in {"queued", "retry"}:
        return "Queued", "chip-amber"
    if state == "failed":
        return "Failed", "chip-red"

    enabled = True if config is None else bool(getattr(config, enabled_attr, True))
    if not enabled:
        has_default = bool((getattr(config, default_attr, "") or "").strip()) if config else False
        return ("Default" if has_default else "Off"), "chip-dim"
    return "Pending", "chip-dim"


def _queue_item(video, phase, label, progress, *, config, ai_status=None, fallback_title=""):
    title = ((getattr(video, "title", "") or fallback_title) or "Untitled video").strip()
    caption_status, caption_chip = _generation_state(video, ai_status, config, "caption")
    hashtag_status, hashtag_chip = _generation_state(video, ai_status, config, "hashtag")
    return {
        "video_id": getattr(video, "id", None),
        "title": title,
        "phase": phase,
        "phase_label": label,
        "progress": progress,
        "caption_status": caption_status,
        "caption_chip": caption_chip,
        "hashtag_status": hashtag_status,
        "hashtag_chip": hashtag_chip,
    }


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _dashboard_queue_context(user_id, user_info, videos, live_phases=None) -> dict:
    """Everything the two queue cards need, from Postgres + Redis."""
    config = getattr(user_info, "userConfig", None) if user_info else None

    try:
        pending_rows = await prisma.video.find_many(
            where={"userId": user_id, "upload_status": "Pending"},
            order={"id": "asc"},
            take=50,
        )
    except Exception:  # noqa: BLE001 - the dashboard must render even if this fails
        logger.exception("Could not load pending videos for user %s", user_id)
        pending_rows = []

    # A row without scheduled_at publishes on the next run, so it comes first.
    pending_rows.sort(
        key=lambda row: _as_utc(getattr(row, "scheduled_at", None))
        or datetime(1970, 1, 1, tzinfo=timezone.utc)
    )

    ready_pending = [
        row for row in pending_rows
        if (getattr(row, "cloudinary_url", "") or "") and not queued_job_id(row)
    ]
    next_video = ready_pending[0] if ready_pending else None
    next_status = await ai_queue.get_status(next_video.id) if next_video else None
    next_at, next_label = _next_schedule(config, next_video)

    active = await ai_queue.active_statuses(user_id)
    by_id = {video.id: video for video in (videos or [])}
    missing = [
        video_id for item in active
        if (video_id := _safe_int(item.get("videoId"))) and video_id not in by_id
    ]
    if missing:
        try:
            extra = await prisma.video.find_many(where={"id": {"in": missing}})
            by_id.update({video.id: video for video in extra})
        except Exception:  # noqa: BLE001
            logger.exception("Could not load videos for active AI jobs")

    now = datetime.now(timezone.utc)
    items = []
    seen: set[int] = set()

    for video in videos or []:
        if queued_job_id(video):
            phase = (live_phases or {}).get(str(video.id)) or "queued"
            progress, label = DOWNLOAD_PHASES.get(phase, DOWNLOAD_PHASES["queued"])
            items.append(_queue_item(video, phase, label, progress, config=config))
            seen.add(video.id)
            continue

        if _video_status(video) == "Pending" and (video.cloudinary_url or ""):
            scheduled = _as_utc(getattr(video, "scheduled_at", None))
            if scheduled is None or scheduled <= now:
                items.append(
                    _queue_item(
                        video,
                        "publish_ready",
                        "Waiting for the next publish run",
                        95,
                        config=config,
                    )
                )
                seen.add(video.id)

    # Pending rows older than the page's video list still belong in the queue.
    for video in ready_pending:
        if video.id in seen:
            continue
        scheduled = _as_utc(getattr(video, "scheduled_at", None))
        if scheduled is None or scheduled <= now:
            items.append(
                _queue_item(
                    video,
                    "publish_ready",
                    "Waiting for the next publish run",
                    95,
                    config=config,
                )
            )
            seen.add(video.id)

    for item in active:
        video_id = _safe_int(item.get("videoId"))
        if not video_id or video_id in seen:
            continue
        state = str(item.get("status") or "queued").lower()
        processing = state == "processing"
        items.append(
            _queue_item(
                by_id.get(video_id),
                "ai_processing" if processing else "ai_queued",
                "Writing caption and trending hashtags" if processing else "Caption and hashtags queued",
                65 if processing else 25,
                config=config,
                ai_status=item,
                fallback_title=f"Video #{video_id}",
            )
        )
        seen.add(video_id)

    items.sort(
        key=lambda entry: (
            0 if entry["phase"] in {"ai_processing", "downloading", "uploading"} else 1,
            -entry["progress"],
        )
    )

    snapshot = await ai_queue.snapshot()
    caption_status, caption_chip = _generation_state(next_video, next_status, config, "caption")
    hashtag_status, hashtag_chip = _generation_state(next_video, next_status, config, "hashtag")

    return {
        "next_run_at": next_at.isoformat() if next_at else None,
        "next_run_label": next_label,
        "next_video_title": (
            (getattr(next_video, "title", "") or "Untitled video")
            if next_video else "No pending video"
        ),
        "next_scheduled_video": next_video,
        "caption_status": caption_status,
        "caption_chip": caption_chip,
        "hashtag_status": hashtag_status,
        "hashtag_chip": hashtag_chip,
        "queue_items": items[:5],
        "queue_count": len(items),
        "queue_hidden": max(0, len(items) - 5),
        "ai_queue_enabled": snapshot.get("enabled", False),
        "ai_worker_online": snapshot.get("online", False),
        "ai_queue_depth": snapshot.get("queued", 0),
        "ai_queue_processing": snapshot.get("processing", 0),
    }


# ==========================================
# PLAN / ASSET HELPERS (playground + clipper)
# ==========================================

PUBLISH_TARGETS = ("Youtube", "Tiktok", "Facebook", "Instagram")

# Clips are uploaded straight from the browser to this folder. The prefix is
# checked when the upload is registered so nobody can claim someone else's file.
CLIP_FOLDER = "social-bot-clips"


def _plan_key_of(user) -> str:
    plan = getattr(user, "plan", None)
    plan_type = getattr(plan, "type", None)
    key = getattr(plan_type, "value", plan_type)
    return str(key or "free").lower()


async def _load_plan_key(user) -> str:
    """get_current_user() does not include the plan, so fetch it when missing."""
    if getattr(user, "plan", None) is None:
        fresh = await prisma.user.find_unique(
            where={"id": user.id}, include={"plan": True}
        )
        if fresh is None:
            return "free"
        return _plan_key_of(fresh)
    return _plan_key_of(user)


def _parse_scheduled_at(value: Optional[str]) -> Optional[datetime]:
    """ISO-8601 (browser sends UTC) -> aware UTC. Empty/past means 'now'.

    Prisma stores DateTime as UTC; keeping the tzinfo on the value makes the
    round-trip unambiguous regardless of the server's local timezone.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed if parsed > datetime.now(timezone.utc) else None


def _clean_targets(values) -> list:
    """Accept a CSV string, a list, or garbage, and always return a valid list."""
    if isinstance(values, str):
        values = values.split(",")
    cleaned = []
    for value in values or []:
        name = str(value).strip()
        if name in PUBLISH_TARGETS and name not in cleaned:
            cleaned.append(name)
    return cleaned or ["Youtube"]


async def _latest_user_video(user_id: int):
    """Most recent Cloudinary-backed video â€” the sample the preview renders on."""
    rows = await prisma.video.find_many(
        where={"userId": user_id},
        order={"id": "desc"},
        take=12,
    )
    for row in rows:
        if getattr(row, "cloudinary_id", "") and getattr(row, "cloudinary_url", ""):
            return row
    return None


# Gravity values the stored config may take (keys of the transformer maps).
TEXT_POSITIONS = tuple(TEXT_GRAVITY.keys())
CROP_GRAVITIES = tuple(CROP_GRAVITY.keys())

# The settings modal still speaks the legacy anti-copyright vocabulary.
LEGACY_GRAVITY = {
    "center": "center", "top": "north", "bottom": "south",
    "top-left": "north_west", "top-right": "north_east",
    "bottom-left": "south_west", "bottom-right": "south_east",
}
LEGACY_POSITION = {v: k for k, v in LEGACY_GRAVITY.items()}


def _clamp_int(value, low: int, high: int, fallback: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _clamp_float(value, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return round(max(low, min(high, number)), 1)


def _json_dict(value) -> dict:
    """Prisma Json columns come back as dicts, or strings when stored via json.dumps."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _merged_playground_config(stored) -> dict:
    """Stored defaults layered over DEFAULT_PLAYGROUND_CONFIG (section by section)."""
    merged = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in DEFAULT_PLAYGROUND_CONFIG.items()
    }
    for section, value in _json_dict(stored).items():
        if isinstance(value, dict) and isinstance(merged.get(section), dict):
            merged[section].update(value)
        else:
            merged[section] = value
    return merged


def _normalize_playground_config(payload: dict) -> dict:
    """Clamp whatever the browser sent into a config the transformer can trust."""
    audio = payload.get("audioControls") or {}
    trim = payload.get("trim") or {}
    resize = payload.get("resize") or {}
    gravity = payload.get("gravity") or {}
    text = payload.get("textOverlay") or {}
    pixel = payload.get("pixel") or {}
    quality = str((payload.get("quality") or {}).get("quality") or "auto")

    frequency = str(audio.get("frequency") or "").strip()
    if frequency not in ("", "44100", "32000", "22050", "16000"):
        frequency = "22050"
    if quality not in ("auto", "90", "75", "60"):
        quality = "auto"
    crop_mode = str(resize.get("cropMode") or "crop").lower()
    if crop_mode not in ("none", "scale", "crop", "thumb"):
        crop_mode = "crop"
    position = str(text.get("position") or "bottom").lower()
    if position not in TEXT_POSITIONS:
        position = "bottom"
    gravity_key = str(gravity.get("gravity") or "center").lower()
    if gravity_key not in CROP_GRAVITIES:
        gravity_key = "center"

    return {
        "enabled": bool(payload.get("enabled", True)),
        "audioControls": {
            "volume": _clamp_int(audio.get("volume"), -100, 200, 3),
            "frequency": frequency,
            "removeAudio": bool(audio.get("removeAudio")),
        },
        "trim": {
            "startOffset": _clamp_float(trim.get("startOffset"), 0, 15, 0),
            "duration": _clamp_float(trim.get("duration"), 0, 60, 0),
        },
        "resize": {
            "cropMode": crop_mode,
            "width": _clamp_int(resize.get("width"), 240, 1920, 720),
            "height": _clamp_int(resize.get("height"), 240, 1920, 1280),
        },
        "gravity": {"gravity": gravity_key},
        "textOverlay": {
            "enabled": bool(text.get("enabled", True)),
            "text": str(text.get("text") or "").strip()[:60],
            "fontSize": _clamp_int(text.get("fontSize"), 12, 160, 36),
            "color": normalize_color(text.get("color")),
            "opacity": _clamp_int(text.get("opacity"), 1, 100, 60),
            "position": position,
        },
        "pixel": {
            "vignette": _clamp_int(pixel.get("vignette"), 0, 100, 10),
            "noise": _clamp_int(pixel.get("noise"), 0, 100, 3),
        },
        "quality": {"quality": quality},
    }


def _legacy_blocks_from_playground(config: dict) -> dict:
    """Mirror the playground defaults into the legacy anti-copyright columns.

    The upload pipeline prefers playgroundTransformations, but the settings
    modal still reads these columns â€” keeping them in sync avoids drift.
    """
    audio = config.get("audioControls") or {}
    text = config.get("textOverlay") or {}
    pixel = config.get("pixel") or {}
    return {
        "audioControls": json.dumps({
            "speed": 100 + int(audio.get("volume") or 0),
            "frequency": audio.get("frequency") or "",
            "mute": bool(audio.get("removeAudio")),
        }),
        "textOverlayControls": json.dumps({
            "text": text.get("text") or "",
            "color": text.get("color") or "#FFFFFF",
            "opacity": text.get("opacity") or 60,
            "position": LEGACY_GRAVITY.get(str(text.get("position") or "bottom"), "south"),
            "yGap": 120,
        }),
        "pixelDistortionControls": json.dumps({
            "vignette": pixel.get("vignette") or 0,
            "noise": pixel.get("noise") or 0,
        }),
    }


def _apply_legacy_to_playground(existing, request: "DynamicEditRequest") -> Optional[str]:
    """If playground defaults exist, fold a settings-modal save into them."""
    stored = _json_dict(existing)
    if not stored:
        return None

    config = _merged_playground_config(stored)
    audio = request.audioControls or {}
    text = request.textOverlayControls or {}
    pixel = request.pixelDistortionControls or {}

    if audio:
        speed = _clamp_int(audio.get("speed"), 0, 300, 103)
        config["audioControls"].update({
            "volume": max(-100, min(200, speed - 100)),
            "frequency": str(audio.get("frequency") or "").strip(),
            "removeAudio": bool(audio.get("mute")),
        })
    if text:
        config["textOverlay"].update({
            "enabled": bool(str(text.get("text") or "").strip()),
            "text": str(text.get("text") or "")[:60],
            "color": normalize_color(text.get("color")),
            "opacity": _clamp_int(text.get("opacity"), 1, 100, 60),
            "position": LEGACY_POSITION.get(str(text.get("position") or "south"), "bottom"),
        })
    if pixel:
        config["pixel"].update({
            "vignette": _clamp_int(pixel.get("vignette"), 0, 100, 0),
            "noise": _clamp_int(pixel.get("noise"), 0, 100, 0),
        })
    return json.dumps(config)


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user  
    try: 
        
        videos  = await prisma.video.find_many(
            where={
                "userId":current_user.id
            },
            order={
                 "updatedAt":"desc"   
            },
            take=10
        )
        total_videos_count = await prisma.video.count(
            where={
            "userId": current_user.id
            }
        )

        pending_videos_count = await prisma.video.count(
            where={
            "userId": current_user.id,
            "upload_status": "Pending"
            }
        )
        canceled_videos_count = await prisma.video.count(
            where={
            "userId": current_user.id,
            "upload_status": "Failed"
            }
        )
 

        user_info = await prisma.user.find_unique(
            where={
                "id": current_user.id
            },
            include={
                "plan": True,
                "usage": True,
                "userConfig": True,
            },
    )
  

        context = _build_dashboard_context(
            request,
            user_info,
            videos=videos,
            total_videos_count=total_videos_count,
            pending_videos_count=pending_videos_count,
            canceled_videos_count=canceled_videos_count,
        )
        # Redis can be slow or down; the cards must still render from defaults.
        try:
            context.update(
                await asyncio.wait_for(
                    _dashboard_queue_context(current_user.id, user_info, videos),
                    timeout=4,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not build the live queue context")

        return templates.TemplateResponse("dashboard/index.html", context)

    except HTTPException as e:
        return templates.TemplateResponse("500.html", {"request": request, "title": "Home Page","error":e})

@router.get("/videos", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user 
    videos = await prisma.video.find_many(
        where={"userId": current_user.id},
        order={"updatedAt": "desc"},
        take=50,
    )
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    total_videos_count = await prisma.video.count(where={"userId": current_user.id})
    pending_videos_count = await prisma.video.count(where={"userId": current_user.id, "upload_status": "Pending"})
    canceled_videos_count = await prisma.video.count(where={"userId": current_user.id, "upload_status": "Failed"})
    return templates.TemplateResponse(
        "dashboard/videos.html",
        _build_dashboard_context(
            request,
            user_info,
            videos=videos,
            total_videos_count=total_videos_count,
            pending_videos_count=pending_videos_count,
            canceled_videos_count=canceled_videos_count,
        ),
    )

@router.get("/settings", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user 
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    if user_info is None:
        raise HTTPException(status_code=404, detail="User not found")
    return templates.TemplateResponse(
        "dashboard/settings.html",
        _build_dashboard_context(request, user_info),
    )

@router.get("/plan", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user 
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    return templates.TemplateResponse(
        "dashboard/plan.html",
        _build_dashboard_context(request, user_info),
    )


@router.get("/playground", response_class=HTMLResponse)
async def video_playground(request:Request, current_user: dict = Depends(get_current_user)):
    """Default transformations applied to every video uploaded to Cloudinary."""
    if isinstance(current_user, RedirectResponse):
        return current_user 
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    if user_info is None:
        raise HTTPException(status_code=404, detail="User not found")
    plan_key = _plan_key_of(user_info)

    dynamic_edits = await prisma.dynamicedit.find_unique(
        where={"userId": current_user.id}
    )
    stored = getattr(dynamic_edits, "playgroundTransformations", None)
    config = _merged_playground_config(stored)

    # Prefill the watermark with something personal instead of a placeholder.
    recommended = _merged_playground_config({})
    handle = (
        getattr(user_info, "fullname", "")
        or (user_info.email or "").split("@")[0]
    ).strip()
    handle = "".join(ch for ch in handle if ch.isalnum() or ch in "._")[:20]
    if handle:
        recommended["textOverlay"]["text"] = f"@{handle}"
    if not _json_dict(stored):
        config = _merged_playground_config(recommended)

    # The preview renders on the newest video in the library; brand-new
    # accounts see the form with a hint until their first upload lands.
    latest = await _latest_user_video(current_user.id)
    preview_url = ""
    if latest:
        preview_url = (
            build_playground_preview_url(latest.cloudinary_id, config)
            if config.get("enabled", True)
            else latest.cloudinary_url
        )

    pending = await prisma.video.count(
        where={"userId": current_user.id, "upload_status": "Pending"}
    )
    context = _build_dashboard_context(
        request, user_info, pending_videos_count=pending
    )
    context.update({
        "title": "Upload Playground",
        "playground_defaults": config,
        "recommended_defaults": recommended,
        "has_preview": bool(latest),
        "preview_title": (latest.title or f"Video #{latest.id}") if latest else "",
        "preview_url": preview_url,
        "pending_videos_count": pending,
        "plan_slots": plan_limits(plan_key)["max_scheduled_videos"],
        "plan_key": plan_key,
    })
    return templates.TemplateResponse("dashboard/playground.html", context)


@router.post("/upload")    
async def upload_video(
    file: UploadFile = File(...), 
    current_user : dict = Depends(get_current_user),
    upload_destinations: list = Depends(verify_upload_target)):

    if isinstance(current_user, RedirectResponse):
        return current_user   
    
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")

    # Reject before the expensive Cloudinary upload when the user is already at their limit
    try:
        await assert_can_schedule(current_user.id)
    except QuotaExceededError as e:
        raise HTTPException(status_code=402, detail=str(e))

    try:
        # Defaults saved in the Playground are baked into every upload, so the
        # file dropped here gets the same anti-copyright treatment as URL jobs.
        transformations = await user_transformations(current_user.id) or None
        # Upload to Cloudinary off the event loop
        result = await asyncio.to_thread(
            cloudinary.uploader.upload,
            file.file,
            resource_type="video",
            folder="social-bot-videos",
            transformation=transformations,
        )
        # public_id (not asset_id) is what Cloudinary requires to delete the asset later
        await create_video(current_user.id,result["secure_url"],result.get("public_id") or result["asset_id"],file.filename,upload_destinations)

        return Response(content={"message":"Video uploaded success!"},status_code=201)
        
         
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


# Upload video file from Url across Tiktok,youtube and facebook 
@router.post("/upload-from-url")
async def upload_url_video(
    background_tasks: BackgroundTasks,
    platform: str = Depends(validate_video_url),
    url: str = Query(...),
    upload_destinations: list = Depends(verify_upload_target),
    current_user: dict = Depends(get_current_user)
    ):

    if isinstance(current_user,RedirectResponse):
        return current_user

    # Serverless hosts kill background work once the response is sent, so hand the
    # job to the worker inside the request. Falling back to the local pipeline is
    # only useful on a long-lived host: on Vercel it would be killed at maxDuration.
    if worker_enabled():
        try:
            await assert_can_schedule(current_user.id)
        except QuotaExceededError as e:
            raise HTTPException(status_code=402, detail=str(e))

        dispatched = await dispatch_download(
            user_id=current_user.id,
            video_url=url,
            platform=platform,
            upload_destinations=upload_destinations,
            transformation=await user_transformations(current_user.id),
        )
        if dispatched and dispatched.get("jobId"):
            # Queue visibility: the row exists before the download starts, and the
            # worker's webhook fills it in instead of inserting a second one.
            try:
                video = await create_queued_video(
                    user_id=current_user.id,
                    job_id=dispatched["jobId"],
                    title=queued_title(platform, url),
                    upload_destinations=upload_destinations,
                    platform=platform,
                )
            except QuotaExceededError as e:
                raise HTTPException(status_code=402, detail=str(e))
            return {
                "message": "Download queued",
                "jobId": dispatched["jobId"],
                "videoId": video.id,
                "title": video.title,
            }

        if worker_required():
            # Never pretend to download here: the request would burn maxDuration
            # and the user would see nothing in the queue.
            logger.error(
                "Worker configured but unreachable (%s) - refusing in-process download",
                last_dispatch_error(),
            )
            raise HTTPException(
                status_code=502,
                detail=f"Download worker unreachable: {last_dispatch_error() or 'unknown error'}",
            )
        logger.warning(
            "Worker dispatch failed (%s) - downloading inside the web process",
            last_dispatch_error(),
        )
    elif running_on_vercel():
        logger.error("WORKER_SERVICE_URL / WORKER_TOKEN are not set on this deployment")
        raise HTTPException(
            status_code=503,
            detail=(
                "Download worker is not configured. Set WORKER_SERVICE_URL and "
                "WORKER_TOKEN on this deployment, then redeploy."
            ),
        )

    # Calling a class that has many Facebook, Tiktok, Youtube and Instagram video download and uploader to Cloudinary
    down_and_upload_from = FromPlatform(video_url=url,user_id=current_user.id,upload_targets=upload_destinations)
    # Schedule the coroutine *function*; awaiting it here would run the work inline
    # and then hand a non-callable result to add_task().
    background_tasks.add_task(down_and_upload_from.run, platform)
    return {"message":"Process started"}


@router.get("/api/queue")
async def queue_status(view: int = Query(0), current_user: dict = Depends(get_current_user)):
    """Live queue for the dashboard: DB state plus the worker's in-flight phases.

    ``view=1`` additionally returns the two home-page cards (next scheduled run
    and the processing queue) so they can refresh without a page reload.
    """
    if isinstance(current_user, RedirectResponse):
        raise HTTPException(status_code=401, detail="Not authenticated")

    videos = await prisma.video.find_many(
        where={"userId": current_user.id},
        order={"id": "desc"},
        take=12,
    )

    items = []
    in_flight = []
    for video in videos:
        status = str(getattr(video.upload_status, "value", video.upload_status) or "")
        item = {
            "id": video.id,
            "title": video.title,
            "jobId": queued_job_id(video),
            "status": status,
            "phase": None,
            "error": None,
            "url": video.cloudinary_url or "",
            "updatedAt": video.updatedAt.isoformat() if getattr(video, "updatedAt", None) else None,
        }
        items.append(item)
        if item["jobId"]:
            in_flight.append(item)

    if in_flight and worker_enabled():
        remote = await asyncio.gather(
            *(fetch_job_status(item["jobId"]) for item in in_flight),
            return_exceptions=True,
        )
        phase_map = {
            "queued": "queued",
            "running": "downloading",
            "uploading": "uploading",
            "done": "done",
            "failed": "failed",
            "delivery_failed": "delivery_failed",
        }
        for item, job in zip(in_flight, remote):
            if not isinstance(job, dict):
                continue
            item["phase"] = phase_map.get(job.get("status"), job.get("status"))
            item["error"] = job.get("error")
            if job.get("title"):
                item["title"] = job["title"]

    fallback = {"Uploaded": "uploaded", "Pending": "ready", "Failed": "failed", "Deleted": "deleted"}
    for item in items:
        if item["phase"]:
            continue
        # A row that still carries a job marker is a download placeholder: it is
        # Pending in the database but the download has not reported back yet, so
        # calling it "ready" would tell the dashboard the job already finished
        # (and the videos table reloads the page on that signal).
        item["phase"] = (
            "queued" if item["jobId"]
            else fallback.get(item["status"], (item["status"] or "").lower())
        )

    payload = {"videos": items, "workerEnabled": worker_enabled()}
    if not view:
        return payload

    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "userConfig": True},
    )
    phases = {str(item["id"]): item["phase"] for item in items if item.get("phase")}
    try:
        live = await asyncio.wait_for(
            _dashboard_queue_context(current_user.id, user_info, videos, live_phases=phases),
            timeout=4,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not refresh the queue cards")
        live = {}

    payload["queue"] = {
        "items": live.get("queue_items", []),
        "count": live.get("queue_count", 0),
        "hidden": live.get("queue_hidden", 0),
        "worker": {
            "enabled": live.get("ai_queue_enabled", False),
            "online": live.get("ai_worker_online", False),
            "queued": live.get("ai_queue_depth", 0),
            "processing": live.get("ai_queue_processing", 0),
        },
    }
    payload["next"] = {
        "runLabel": live.get("next_run_label"),
        "videoTitle": live.get("next_video_title"),
        "captionStatus": live.get("caption_status"),
        "captionChip": live.get("caption_chip"),
        "hashtagStatus": live.get("hashtag_status"),
        "hashtagChip": live.get("hashtag_chip"),
        "videoId": getattr(live.get("next_scheduled_video"), "id", None),
    }
    return payload


@router.post("/api/videos/{video_id}/generate")
async def generate_video_copy(
    video_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Regenerate the caption + trending hashtags for one video.

    The job goes to Redis when the queue is available (``wroker.py`` runs it),
    otherwise it runs in the background of this process.
    """
    if isinstance(current_user, RedirectResponse):
        raise HTTPException(status_code=401, detail="Not authenticated")

    video = await prisma.video.find_unique(where={"id": video_id})
    if video is None or video.userId != current_user.id:
        raise HTTPException(status_code=404, detail="Video not found")

    result = await schedule_video_ai(video, force=True, reason="manual")
    if result.get("error"):
        raise HTTPException(status_code=502, detail="Generation could not be started")
    return {"ok": True, **result}


@router.get("/api/worker/diagnostics")
async def worker_diagnostics(current_user: dict = Depends(get_current_user)):
    """One-click answer to 'is this deployment actually talking to the worker?'."""
    if isinstance(current_user, RedirectResponse):
        raise HTTPException(status_code=401, detail="Not authenticated")
    health = await fetch_worker_health()
    return {**worker_status(), **health}


# This is scheduled by DBOS
# Upload pexels random video
@router.get("/trigger-pexels-upload")
async def trigger_pexels_upload(background_tasks:BackgroundTasks,per_page : int = 5):
    return await UploadPexelVideo(background_tasks,per_page)



# This is Scheduled by DBOS 
@router.post("/trigger-upload")
async def trigger_upload(background_tasks: BackgroundTasks,user_id:int,secret:str):

    if secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    uploaded_users=[] # add uploaded video user id

    # Only publish rows whose scheduled slot has arrived: the clipper and the
    # playground can park videos hours or days ahead of the cron run.
    pending_videos = await prisma.video.find_many(
        where={
            "upload_status":"Pending",
            "AND":[
                {"upload_status":"Pending"},
                {"userId":user_id},
                {"scheduled_at":{"lte":datetime.now(timezone.utc)}},
            ]
        },
        order={
            "created_at":"asc"
        },
         include={
             'user':{
                "include":{
                    "userConfig":True
                }
             }
         }
    )

    videos_of_a_users = 0
    for video in pending_videos:
        if queued_job_id(video) or not video.cloudinary_url:
            # Still downloading on the worker (or a failed placeholder): never
            # hand an empty URL to the publisher.
            continue
        if not video.user.id in  uploaded_users: # check if the user videos is already uploaded or not
            # Added this to background task to upload without waiting for the response
            background_tasks.add_task(UploadVideo(video.user,video).upload)
            videos_of_a_users +=1
            # Upload 2 videos of each user daily to avoid cloudinary upload limit and also to avoid uploading all videos of a user if he has many pending videos
            if(videos_of_a_users >= 2):
                uploaded_users.append(video.user.id)
                videos_of_a_users = 0
                
    return {"message": "upload process triggered"}

 

# This is be trigger by schedule on DBOS
@router.post("/trigger-deleted-uploaded-files")
async def trigger_delete_uploaded_files(background_tasks: BackgroundTasks):
    background_tasks.add_task(delete_uploaded_files)
    print("Delete uploaded files triggered")
    return {"message":"Delete uploaded files triggered"}


 

# ==========================================
# API KEY MANAGEMENT ENDPOINTS
# ==========================================


@router.post("/api/settings/generate-api-key")
async def generate_api_key(
    name: str = Query("Discovery Service"),
    current_user = Depends(get_current_user)
):
    """Generate an API key for external video discovery service"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Generate secure random token
        raw_token = secrets.token_urlsafe(32)
        
        # Hash it for storage (never store plain tokens)
        hashed = hashlib.sha256(raw_token.strip().encode()).hexdigest()
        
        updated = await prisma.userapikey.create(
            data={
                "userId": current_user.id,
                "apiKey": hashed,
                "raw_token": raw_token.strip(),  # Store raw token for one-time return
                "name": name
            }
        )

        # Return the plain token only once (user must save it)
        return {
            "success": True,
            "message": "API key generated. Save it securely!",
            "apiKey": raw_token.strip(),  # Only return once
            "userId": current_user.id,
            "expiresAt": updated.expiresAt
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/settings/api-key-status")
async def get_api_key_status(current_user = Depends(get_current_user)):
    """Check if user has an active API key"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        api_key = await prisma.userapikey.find_many(
            where={"userId": current_user.id}
        )

        result = []
        for key in api_key:
             result.append({
                "id": key.id,
                "hasApiKey": True,
                "name": key.name,
                "isActive": key.isActive,
                "lastUsedAt": key.lastUsedAt,
                "createdAt": key.createdAt
            })
       
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/settings/api-key")
async def revoke_api_key(current_user = Depends(get_current_user),id: int = Query(...)):
    """Revoke user's API key"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")
        print(current_user.id)
        await prisma.userapikey.update(
            where={"id":id},
            data={"isActive": False}
        )

        return {"success": True, "message": "API key revoked"}

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# UPLOAD DEFAULTS ENDPOINT
# ==========================================

@router.get("/api/settings/upload-defaults")
async def get_upload_defaults(current_user = Depends(get_current_user)):
    """Get user's default upload destinations"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        user_config = await prisma.userconfig.find_unique(
            where={"userId": current_user.id}
        )

        if not user_config:
            return {
                "success": True,
                "destinations": ["tiktok"],  # Default
                "captionGenEnabled": True,
                "hashtagGenEnabled": True
            }

        destinations = user_config.defaultUploadDests if isinstance(user_config.defaultUploadDests, list) else ["tiktok"]
        captions = user_config.defaultCaption if isinstance(user_config.defaultCaption, str) else ""
        htags = user_config.defaultHtags if isinstance(user_config.defaultHtags, str) else ""

        return {
            "success": True,
            "destinations": destinations,
            "defaultCaption": captions,
            "defaultHtags": htags,
            "captionGenEnabled": bool(user_config.captionGenEnabled),
            "hashtagGenEnabled": bool(user_config.hashtagGenEnabled),
        }

    except Exception as e:
        logger.error(f"Error fetching upload defaults: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/upload-defaults")
async def save_upload_defaults(
    upload_destinations: list = Query(...),
    caption_gen_enabled: Optional[bool] = Query(None),
    hashtag_gen_enabled: Optional[bool] = Query(None),
    current_user = Depends(get_current_user)
):
    """Save user's default upload destinations"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Receiving ["Youtube,Tiktok"] converting -> ["Youtube","Tiktok"]
        upload_destinations = [
            item.strip()
            for value in upload_destinations
            for item in value.split(',')
        ]
        print("Received upload destinations:", upload_destinations)

        # Validate destinations
        valid_destinations = ["Tiktok", "Youtube", "Facebook", "Instagram"]
        destinations = [d for d in upload_destinations if d in valid_destinations]
        print("after destinations:", destinations)

        if not destinations:
            raise HTTPException(status_code=400, detail="At least one valid destination required")

        # Get or create userconfig
        user_config = await prisma.userconfig.find_unique(
            where={"userId": current_user.id}
        )

        if user_config:
            # Update existing
            changes = {"defaultUploadDests": destinations}
            if caption_gen_enabled is not None:
                changes["captionGenEnabled"] = bool(caption_gen_enabled)
            if hashtag_gen_enabled is not None:
                changes["hashtagGenEnabled"] = bool(hashtag_gen_enabled)
            await prisma.userconfig.update(
                where={"userId": current_user.id},
                data=changes
            )
        else:
            # Create new
            await prisma.userconfig.create(
                data={
                    "userId": current_user.id,
                    "defaultUploadDests": destinations,
                    "captionGenEnabled": True if caption_gen_enabled is None else bool(caption_gen_enabled),
                    "hashtagGenEnabled": True if hashtag_gen_enabled is None else bool(hashtag_gen_enabled),
                }
            )

        logger.info(f"âœ… Upload defaults saved for user {current_user.id}: {destinations}")
        return {
            "success": True,
            "message": "Upload defaults saved",
            "destinations": destinations,
            "captionGenEnabled": caption_gen_enabled,
            "hashtagGenEnabled": hashtag_gen_enabled,
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error saving upload defaults: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))




 
# Update video details (title, captions, upload destinations)
@router.put("/api/videos/{video_id}")
async def update_video(
    video_id: int,
    request: UpdateVideoRequest,
    current_user = Depends(get_current_user)
):
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Check if video exists and belongs to the current user
        video = await prisma.video.find_unique(
            where={"id": video_id}
        )

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        if video.userId != current_user.id:
            raise HTTPException(status_code=403, detail="Unauthorized - Video does not belong to current user")

        # Prevent editing if video is already uploaded
        if video.upload_status == "Uploaded":
            raise HTTPException(status_code=409, detail="Cannot edit videos that have already been uploaded")

        # Prepare update data
        update_data = {}
        if request.title is not None:
            update_data["title"] = request.title
        if request.captions is not None:
            update_data["captions"] = request.captions
        if request.upload_destinations is not None and len(request.upload_destinations) > 0:
            update_data["upload_destinations"] = request.upload_destinations

        # Update the video
        updated_video = await prisma.video.update(
            where={"id": video_id},
            data=update_data
        )

        return {
            "message": "Video updated successfully",
            "video": {
                "id": updated_video.id,
                "title": updated_video.title,
                "captions": updated_video.captions,
                "upload_destinations": updated_video.upload_destinations
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error updating video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update video: {str(e)}")


@router.post("/api/videos/{video_id}/reschedule")
async def reschedule_video(
    video_id: int,
    current_user = Depends(get_current_user)
):
    """Reschedule a failed video by updating its status to Pending"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Check if video exists and belongs to the current user
        video = await prisma.video.find_unique(
            where={"id": video_id}
        )

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        if video.userId != current_user.id:
            raise HTTPException(status_code=403, detail="Unauthorized - Video does not belong to current user")

        # Check if video status is Failed
        if video.upload_status != "Failed":
            raise HTTPException(status_code=409, detail="Can only reschedule videos with Failed status")

        # Update status to Pending
        updated_video = await prisma.video.update(
            where={"id": video_id},
            data={"upload_status": "Pending"}
        )

        return {
            "message": "Video rescheduled successfully",
            "video": {
                "id": updated_video.id,
                "upload_status": updated_video.upload_status
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error rescheduling video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to reschedule video: {str(e)}")


# Connect Buffer - fetch profiles via GraphQL
@router.patch("/buffer-connect")
async def buffer_connect(body: BufferConnectRequest, current_user = Depends(get_current_user)):
    try:
        buffer_api_key = body.bufferApiKey.strip()

        if not buffer_api_key:
            raise HTTPException(status_code=400, detail="Buffer API key is required")

        # Fetch every channel first so a bad key cannot leave the profile half
        # configured (old code saved the key before validating it).
        channels = await asyncio.to_thread(get_profiles_graphql, buffer_api_key)
        if not channels:
            raise HTTPException(status_code=400, detail="Failed to fetch Buffer profiles. No channels found.")

        user = await prisma.user.find_unique(where={"id": current_user.id})
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Merge every channel in one pass. Each channel is stored under its
        # Buffer service ("tiktok", "youtube", ...) so it lines up with
        # UploadTargets; when a service has several channels the last one wins
        # because the publisher stores a single id per platform.
        stored = user.credentials if isinstance(user.credentials, dict) else {}
        credentials = dict(stored)
        synced = {}
        for channel in channels:
            service = str(channel.get("service") or "").lower()
            if not service:
                continue
            synced[service] = channel
            credentials[service] = json.dumps({
                "client_id": channel["id"],
                "client_secret": channel["id"],
                "name": channel.get("name"),
                "service": "buffer",
            })

        # Channels removed in Buffer disappear from the upload targets too.
        # Only records we wrote (service == "buffer") are pruned, so OAuth
        # accounts such as "google" are never touched.
        for service in list(credentials):
            record = credentials[service]
            if isinstance(record, str):
                try:
                    record = json.loads(record)
                except (TypeError, ValueError):
                    continue
            if not isinstance(record, dict) or record.get("service") != "buffer":
                continue
            if service not in synced:
                credentials.pop(service)

        user_config = await prisma.userconfig.find_unique(
            where={"userId": current_user.id}
        )

        if user_config:
            await prisma.userconfig.update(
                where={"userId": current_user.id},
                data={"bufferApiKey": buffer_api_key}
            )
        else:
            await prisma.userconfig.create(
                data={
                    "userId": current_user.id,
                    "bufferApiKey": buffer_api_key
                }
            )

        await prisma.user.update(
            where={"id": user.id},
            data={"credentials": json.dumps(credentials)},
        )

        return {
            "message": f"Buffer connected successfully ({len(synced)} channel(s) synced)",
            "status": "connected",
            "channels": channels,
            "uploadTargets": _connected_upload_targets(credentials),
        }
    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"Failed to connect to Buffer: {str(e)}")


# Dynamic edits for anti-copyright transformations
@router.get("/api/settings/dynamic-edits")
async def get_dynamic_edits(current_user: dict = Depends(get_current_user)):
    """Fetch user's dynamic edit configuration"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    dynamic_edits = await prisma.dynamicedit.find_unique(
        where={"userId": current_user.id}
    )

    
    if not dynamic_edits:
        return {
            "audioControls": {},
            "textOverlayControls": {},
            "pixelDistortionControls": {}
        }
    
    return {
        "audioControls": dynamic_edits.audioControls,
        "textOverlayControls": dynamic_edits.textOverlayControls,
        "pixelDistortionControls": dynamic_edits.pixelDistortionControls
    }


# ==========================================
# VIDEO PLAYGROUND API ENDPOINTS
# ==========================================

@router.post("/api/playground/build-transformation")
async def build_transformation(
    request: PlaygroundTransformationRequest,
    current_user: dict = Depends(get_current_user)
):
    """Build Cloudinary transformation URL from playground settings"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    # This endpoint is kept for backwards compatibility
    # The playground is now UI-only and saves config to dynamicEdits
    # Actual Cloudinary transformations happen during video upload
    try:
        transformer = CloudinaryVideoTransformer(request.video_url)
        
        # Add Video Layers if specified
        if request.overlay_type != "none":
            transformer.add_video_layers(
                overlay_type=request.overlay_type,
                overlay_text=request.overlay_text,
                font_size=request.font_size,
                text_color=request.text_color,
                text_opacity=request.text_opacity
            )
        
        # Add Resizing & Cropping
        if request.crop_mode != "none":
            transformer.add_resizing_cropping(
                crop_mode=request.crop_mode,
                width=request.width,
                height=request.height,
                aspect_ratio=request.aspect_ratio,
                gravity=request.gravity,
                x_offset=request.x_offset,
                y_offset=request.y_offset
            )
        
        # Add Auto Cropping
        if request.auto_crop_type != "none":
            transformer.add_auto_cropping(
                crop_type=request.auto_crop_type,
                width=request.width,
                height=request.height
            )
        
        # Add Trimming
        if request.start_offset or request.end_offset or request.duration:
            transformer.add_trimming(
                start_offset=request.start_offset,
                end_offset=request.end_offset,
                duration=request.duration
            )
        
        # Add Quality & Format
        transformer.add_quality_format(
            quality=request.quality,
            dpr=request.dpr
        )
        
        # Build URL
        transformation_url = transformer.build_url()
        
        return {
            "success": True,
            "url": transformation_url,
            "transformations": transformer.get_transformation_chain()
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Transformation error: {str(e)}")


@router.get("/api/playground/presets")
async def get_transformation_presets(current_user: dict = Depends(get_current_user)):
    """Get pre-built transformation presets for common use cases"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    return {
        "presets": {
            "instagram_reel": {
                "name": "Instagram Reel",
                "description": "Optimized for 9:16 vertical video",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "9:16",
                    "width": 1080,
                    "height": 1920,
                    "quality": "auto:good"
                }
            },
            "youtube_thumbnail": {
                "name": "YouTube Thumbnail",
                "description": "16:9 widescreen format",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "16:9",
                    "width": 1280,
                    "height": 720,
                    "quality": "90"
                }
            },
            "tiktok_vertical": {
                "name": "TikTok Vertical",
                "description": "Optimized vertical format for TikTok",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "9:16",
                    "width": 1080,
                    "height": 1920,
                    "auto_crop_type": "face"
                }
            },
            "square_short": {
                "name": "Square Short",
                "description": "1:1 square format for social media",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "1:1",
                    "width": 1080,
                    "height": 1080,
                    "gravity": "auto"
                }
            },
            "mobile_optimized": {
                "name": "Mobile Optimized",
                "description": "Optimized for mobile viewing",
                "config": {
                    "crop_mode": "scale",
                    "width": 720,
                    "height": 1280,
                    "dpr": "auto",
                    "quality": "auto"
                }
            },
            "high_quality_desktop": {
                "name": "High Quality Desktop",
                "description": "Premium quality for desktop",
                "config": {
                    "crop_mode": "fill",
                    "width": 1920,
                    "height": 1080,
                    "quality": "95",
                    "dpr": "1"
                }
            },
            "with_watermark": {
                "name": "With Watermark",
                "description": "Add brand watermark overlay",
                "config": {
                    "overlay_type": "text",
                    "overlay_text": "@yourbrand",
                    "text_color": "#F97316",
                    "text_opacity": 80,
                    "font_size": 48,
                    "gravity": "south_east"
                }
            },
            "auto_crop_faces": {
                "name": "Auto Crop Faces",
                "description": "AI-powered automatic face detection",
                "config": {
                    "crop_mode": "fill",
                    "auto_crop_type": "face",
                    "width": 1280,
                    "height": 720,
                    "gravity": "auto"
                }
            }
        }
    }


@router.post("/api/settings/dynamic-edits")
async def save_dynamic_edits(request: DynamicEditRequest, current_user: dict = Depends(get_current_user)):
    """Save user's dynamic edit configuration"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        ) 
        
        if existing:
            data = {
                "audioControls": json.dumps(request.audioControls),
                "textOverlayControls": json.dumps(request.textOverlayControls),
                "pixelDistortionControls": json.dumps(request.pixelDistortionControls),
            }
            # The Playground prefers playgroundTransformations at upload time;
            # fold this legacy save into it so both editors stay coherent.
            synced = _apply_legacy_to_playground(
                getattr(existing, "playgroundTransformations", None), request
            )
            if synced is not None:
                data["playgroundTransformations"] = synced
            # Update existing record
            dynamic_edits = await prisma.dynamicedit.update(
                where={"userId": current_user.id},
                data=data
            )
        else:
            # Create new record
            dynamic_edits = await prisma.dynamicedit.create(
                data={
                    "userId": current_user.id,
                    "audioControls": json.dumps(request.audioControls),
                    "textOverlayControls": json.dumps(request.textOverlayControls),
                    "pixelDistortionControls": json.dumps(request.pixelDistortionControls),
                }
            )
        
        return {
            "success": True,
            "message": "Dynamic edits saved successfully",
            "data": {
                "audioControls": json.loads(dynamic_edits.audioControls) if isinstance(dynamic_edits.audioControls, str) else dynamic_edits.audioControls,
                "textOverlayControls": json.loads(dynamic_edits.textOverlayControls) if isinstance(dynamic_edits.textOverlayControls, str) else dynamic_edits.textOverlayControls,
                "pixelDistortionControls": json.loads(dynamic_edits.pixelDistortionControls) if isinstance(dynamic_edits.pixelDistortionControls, str) else dynamic_edits.pixelDistortionControls,
            }
        }
    except Exception as e:
        print(f"Error saving dynamic edits: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save dynamic edits: {str(e)}")


@router.get("/api/playground/init")
async def initialize_playground(current_user: dict = Depends(get_current_user)):
    """Initialize playground - check if user has dynamicEdits and load saved settings"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        if existing:
            # Parse saved playground transformations
            try:
                playground_data = json.loads(existing.playgroundTransformations) if isinstance(existing.playgroundTransformations, str) else existing.playgroundTransformations
            except:
                playground_data = None
            
            return {
                "success": True,
                "hasDynamicEdits": True,
                "savedTransformations": playground_data
            }
        else:
            return {
                "success": True,
                "hasDynamicEdits": False,
                "savedTransformations": None
            }
    except Exception as e:
        print(f"Error initializing playground: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize playground: {str(e)}")


@router.post("/api/playground/activate")
async def activate_playground(request: dict, current_user: dict = Depends(get_current_user)):
    """Activate anti-copyright and create dynamicEdits if it doesn't exist"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        default_data = {
            "audioControls": {
                "enabled": True,
                "codec": "aac",
                "frequency": 44100
            },
            "textOverlayControls": {
                "enabled": True
            },
            "pixelDistortionControls": {
                "enabled": True,
                "noise": 0,
                "vignette": 0
            }
        }
        
        # Default playground config
        default_playground = {
            "video_url": "https://cdn.pixabay.com/video/2022/02/06/106883-674268697_large.mp4",
            "overlay_type": "none",
            "overlay_text": "@brand",
            "font_size": 36,
            "text_color": "#F97316",
            "text_opacity": 100,
            "crop_mode": "none",
            "width": None,
            "height": None,
            "aspect_ratio": "",
            "gravity": "",
            "x_offset": 0,
            "y_offset": 0,
            "start_offset": None,
            "end_offset": None,
            "duration": None,
            "quality": "",
            "dpr": "",
            "auto_crop_type": "none"
        }
        
        if existing:
            # Update with anti-copyright settings
            await prisma.dynamicedit.update(
                where={"userId": current_user.id},
                data={
                    "audioControls": json.dumps(default_data["audioControls"]),
                    "textOverlayControls": json.dumps(default_data["textOverlayControls"]),
                    "pixelDistortionControls": json.dumps(default_data["pixelDistortionControls"]),
                    "playgroundTransformations": json.dumps(default_playground)
                }
            )
        else:
            # Create new record with defaults
            await prisma.dynamicedit.create(
                data={
                    "userId": current_user.id,
                    "audioControls": json.dumps(default_data["audioControls"]),
                    "textOverlayControls": json.dumps(default_data["textOverlayControls"]),
                    "pixelDistortionControls": json.dumps(default_data["pixelDistortionControls"]),
                    "playgroundTransformations": json.dumps(default_playground)
                }
            )
        
        return {
            "success": True,
            "message": "Anti-Copyright settings activated",
            "defaultSettings": default_data
        }
    except Exception as e:
        print(f"Error activating anti-copyright: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to activate: {str(e)}")


@router.post("/api/playground/save")
async def save_playground_transformations(request: PlaygroundConfigRequest, current_user: dict = Depends(get_current_user)):
    """Save playground transformation configuration to dynamicEdits (UI preview only)"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        if not existing:
            # Auto-create if doesn't exist
            await prisma.dynamicedit.create(
                data={
                    "userId": current_user.id,
                    "audioControls": "{}",
                    "textOverlayControls": "{}",
                    "pixelDistortionControls": "{}",
                    "playgroundTransformations": json.dumps(request.dict())
                }
            )
        else:
            # Update existing record with playground config
            await prisma.dynamicedit.update(
                where={"userId": current_user.id},
                data={
                    "playgroundTransformations": json.dumps(request.dict()),
                }
            )
        
        return {
            "success": True,
            "message": "Transformation settings saved successfully",
            "transformations": request.dict()
        }
    except Exception as e:
        print(f"Error saving playground transformations: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}")


@router.get("/api/playground/load")
async def load_playground_transformations(current_user: dict = Depends(get_current_user)):
    """Load playground transformation configuration from dynamicEdits"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        if existing and existing.textOverlayControls:
            try:
                text_controls = json.loads(existing.textOverlayControls) if isinstance(existing.textOverlayControls, str) else existing.textOverlayControls
                playground_data = text_controls.get("playground")
                
                if playground_data:
                    return {
                        "success": True,
                        "transformations": playground_data
                    }
            except:
                pass
        
        return {
            "success": False,
            "message": "No saved playground settings found"
        }
    except Exception as e:
        print(f"Error loading playground transformations: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load: {str(e)}")


# â”€â”€ Download worker ingest â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# The download worker runs outside Vercel (yt-dlp + ffmpeg + Deno + cookies) and
# reports finished uploads here. Requests are authenticated with an HMAC over the
# raw body using INTERNAL_SECRET â€” no session cookie and no secret in the URL.

class WorkerIngestPayload(BaseModel):
    jobId: Optional[str] = None
    status: str = "success"
    reason: Optional[str] = None
    error: Optional[str] = None
    userId: int = 0
    cloudinaryUrl: str = ""
    cloudinaryId: str = ""
    title: str = "Untitled"
    platform: str = ""
    externalId: Optional[str] = None
    uploadDestinations: list[str] = ["Youtube"]


def _verify_worker_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    if not INTERNAL_SECRET or not signature:
        return False
    expected = hmac.new(INTERNAL_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature.split("=", 1)[-1].strip()
    return hmac.compare_digest(expected, provided)


@router.post("/api/worker/ingest")
async def worker_ingest(
    request: Request,
    x_worker_signature: Optional[str] = Header(default=None),
):
    """Accept a finished download/upload from a worker as a Pending video."""
    raw_body = await request.body()
    if not _verify_worker_signature(raw_body, x_worker_signature):
        raise HTTPException(status_code=401, detail="Invalid worker signature")

    try:
        payload = WorkerIngestPayload(**json.loads(raw_body))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}")

    # Failures arrive on the same channel so the queued row never stays stuck.
    if payload.status == "failed":
        queued = await find_queued_video(payload.userId, payload.jobId)
        if queued:
            await fail_queued_video(queued)
            logger.warning(
                "Worker job %s failed for user %s (%s): %s",
                payload.jobId,
                payload.userId,
                payload.reason,
                payload.error,
            )
        return {"ok": True, "videoId": queued.id if queued else None}

    # A placeholder row created at dispatch time is filled in place, so one job
    # maps to exactly one dashboard row and no extra quota slot is consumed.
    queued = await find_queued_video(payload.userId, payload.jobId)
    if queued:
        video = await complete_queued_video(
            queued,
            secure_url=payload.cloudinaryUrl,
            asset_id=payload.cloudinaryId,
            title=payload.title,
            external_id=payload.externalId,
            platform=payload.platform or None,
        )
        logger.info("Worker ingest completed queued video %s for user %s", video.id, payload.userId)
        return {"success": True, "videoId": video.id}

    try:
        video = await create_video(
            user_id=payload.userId,
            secure_url=payload.cloudinaryUrl,
            asset_id=payload.cloudinaryId,
            title=payload.title,
            upload_destinations=payload.uploadDestinations,
            video_id=payload.externalId,
            scraped_from=payload.platform or "unkown",
        )
    except QuotaExceededError as exc:
        logger.warning("Worker ingest rejected for user %s: %s", payload.userId, exc)
        # The worker already uploaded the asset; drop it so storage is not wasted.
        try:
            await asyncio.to_thread(
                cloudinary.uploader.destroy,
                payload.cloudinaryId,
                resource_type="video",
                invalidate=True,
            )
        except Exception:
            logger.exception("Could not delete rejected asset %s", payload.cloudinaryId)
        raise HTTPException(status_code=409, detail=str(exc))

    logger.info("Worker ingest stored video %s for user %s", video.id, payload.userId)
    return {"success": True, "videoId": video.id}


# ==========================================
# PLAYGROUND: DEFAULT UPLOAD TRANSFORMATIONS
# ==========================================
# The Playground no longer bakes a copy of one video: its settings are the
# defaults every future upload goes through (worker, in-process download and
# direct file upload all read the same chain). The preview renders those
# defaults over the newest video in the library.

class PlaygroundDefaultsRequest(BaseModel):
    enabled: bool = True
    audioControls: dict = {}
    trim: dict = {}
    resize: dict = {}
    gravity: dict = {}
    textOverlay: dict = {}
    pixel: dict = {}
    quality: dict = {}


class PlaygroundPreviewRequest(BaseModel):
    """Optional unsaved config; the stored defaults are used when omitted."""
    config: Optional[dict] = None


@router.post("/api/playground/preview")
async def playground_preview(
    payload: PlaygroundPreviewRequest,
    current_user: dict = Depends(get_current_user),
):
    """Cloudinary preview of the upload defaults on the newest owned video."""
    if isinstance(current_user, RedirectResponse):
        raise HTTPException(status_code=401, detail="Not authenticated")

    source = await _latest_user_video(current_user.id)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail="Upload a video first â€” the preview renders on your latest upload",
        )

    config = payload.config
    if isinstance(config, dict) and config:
        config = _normalize_playground_config(config)
    else:
        dynamic_edits = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        config = _merged_playground_config(
            getattr(dynamic_edits, "playgroundTransformations", None)
        )

    enabled = bool(config.get("enabled", True))
    chain = build_playground_transformation_chain(config) if enabled else []
    url = (
        build_playground_preview_url(source.cloudinary_id, config)
        if enabled
        else source.cloudinary_url
    )
    return {
        "success": True,
        "url": url,
        "transformations": chain,
        "enabled": enabled,
        "previewTitle": source.title or f"Video #{source.id}",
    }


@router.post("/api/playground/defaults")
async def save_playground_defaults(
    payload: PlaygroundDefaultsRequest,
    current_user: dict = Depends(get_current_user),
):
    """Persist the defaults applied to every future Cloudinary upload."""
    if isinstance(current_user, RedirectResponse):
        raise HTTPException(status_code=401, detail="Not authenticated")

    config = _normalize_playground_config(payload.dict())
    data = {
        "playgroundTransformations": json.dumps(config),
        # Keep the legacy anti-copyright columns in sync for the settings modal.
        **_legacy_blocks_from_playground(config),
    }

    existing = await prisma.dynamicedit.find_unique(
        where={"userId": current_user.id}
    )
    if existing:
        await prisma.dynamicedit.update(where={"userId": current_user.id}, data=data)
    else:
        await prisma.dynamicedit.create(
            data={"userId": current_user.id, **data}
        )

    chain = build_playground_transformation_chain(config) if config["enabled"] else []
    return {
        "success": True,
        "config": config,
        "transformations": chain,
        "steps": len(chain),
    }


# ==========================================
# SMART CLIPPER
# ==========================================
# The source video never leaves the browser: clips are cut client-side, then
# uploaded directly to Cloudinary with a server signature (Vercel caps request
# bodies, so the file must not pass through the API function).

class ClipCompleteRequest(BaseModel):
    public_id: str
    title: str = ""
    upload_targets: list = ["Youtube"]
    scheduled_at: Optional[str] = None
    source_name: str = ""
    clip_index: int = 0
    clip_total: int = 0


@router.get("/clipper", response_class=HTMLResponse)
async def smart_clipper_page(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Device-served video clipper with plan-based limits."""
    if isinstance(current_user, RedirectResponse):
        return current_user
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    plan_key = _plan_key_of(user_info) if user_info else "free"
    config = clipper_config(plan_key)
    pending = await prisma.video.count(
        where={"userId": current_user.id, "upload_status": "Pending"}
    )

    context = _build_dashboard_context(
        request, user_info, pending_videos_count=pending
    )
    context.update({
        "title": "Smart Clipper",
        "clipper": config,
        "pending_videos_count": pending,
        "plan_slots": plan_limits(plan_key)["max_scheduled_videos"],
        "remaining_slots": max(
            0, plan_limits(plan_key)["max_scheduled_videos"] - pending
        ),
        "upload_targets": list(PUBLISH_TARGETS),
    })
    return templates.TemplateResponse("dashboard/clipper.html", context)


@router.post("/api/clipper/signature")
async def clipper_upload_signature(current_user: dict = Depends(get_current_user)):
    """Signed direct-upload parameters so the browser can post a clip."""
    if isinstance(current_user, RedirectResponse):
        raise HTTPException(status_code=401, detail="Not authenticated")

    plan_key = await _load_plan_key(current_user)
    config = clipper_config(plan_key)
    if not config["enabled"]:
        raise HTTPException(
            status_code=403,
            detail="Smart Clipper is not enabled for your plan",
        )

    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
    if not (cloud_name and api_key and api_secret):
        raise HTTPException(status_code=500, detail="Cloudinary is not configured")

    max_slots = plan_limits(plan_key)["max_scheduled_videos"]
    pending = await prisma.video.count(
        where={"userId": current_user.id, "upload_status": "Pending"}
    )
    remaining = max(0, max_slots - pending)
    if remaining == 0:
        raise HTTPException(
            status_code=402,
            detail=f"Your {plan_key} plan already has {pending}/{max_slots} scheduled slots in use",
        )

    timestamp = int(time.time())
    public_id = f"{CLIP_FOLDER}/{current_user.id}-{uuid.uuid4().hex[:16]}"
    signature = cloudinary.utils.api_sign_request(
        {"public_id": public_id, "timestamp": timestamp}, api_secret
    )

    return {
        "cloudName": cloud_name,
        "apiKey": api_key,
        "timestamp": timestamp,
        "publicId": public_id,
        "signature": signature,
        "uploadUrl": f"https://api.cloudinary.com/v1_1/{cloud_name}/video/upload",
        "maxClipSeconds": config["maxClipSeconds"],
        "maxClips": config["maxClips"],
        "remainingSlots": remaining,
    }


@router.post("/api/clipper/complete")
async def clipper_complete_clip(
    payload: ClipCompleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """Register a finished browser upload as a schedulable video row."""
    if isinstance(current_user, RedirectResponse):
        raise HTTPException(status_code=401, detail="Not authenticated")

    plan_key = await _load_plan_key(current_user)
    config = clipper_config(plan_key)
    if not config["enabled"]:
        raise HTTPException(
            status_code=403,
            detail="Smart Clipper is not enabled for your plan",
        )

    # Only assets handed out by /signature (same user, same folder) may be claimed.
    if not payload.public_id.startswith(f"{CLIP_FOLDER}/{current_user.id}-"):
        raise HTTPException(status_code=400, detail="Clip does not belong to this account")
    if payload.clip_total > config["maxClips"] or payload.clip_index > config["maxClips"]:
        raise HTTPException(
            status_code=400,
            detail=f"Your plan allows up to {config['maxClips']} clips per run",
        )

    try:
        resource = await asyncio.to_thread(
            cloudinary.api.resource, payload.public_id, resource_type="video"
        )
    except Exception as exc:
        logger.warning("Clip %s not found on Cloudinary: %s", payload.public_id, exc)
        raise HTTPException(status_code=404, detail="Clip upload not found on Cloudinary")

    duration = float(resource.get("duration") or 0)

    async def _discard_asset() -> None:
        try:
            await asyncio.to_thread(
                cloudinary.uploader.destroy,
                payload.public_id,
                resource_type="video",
                invalidate=True,
            )
        except Exception:
            logger.exception("Could not discard rejected clip %s", payload.public_id)

    if duration and duration > config["maxClipSeconds"] + 2:
        await _discard_asset()
        raise HTTPException(
            status_code=400,
            detail=f"Clips must be {config['maxClipSeconds']}s or shorter",
        )

    try:
        await assert_can_schedule(current_user.id)
    except QuotaExceededError as exc:
        await _discard_asset()
        raise HTTPException(status_code=402, detail=str(exc))

    # Store an mp4 delivery URL: browsers upload webm, but the publisher hands
    # these URLs to YouTube/Buffer which expect mp4.
    url = cloudinary.utils.cloudinary_url(
        payload.public_id, resource_type="video", format="mp4", secure=True
    )[0]
    when = _parse_scheduled_at(payload.scheduled_at)

    title = (payload.title or "").strip()
    if not title:
        base = (payload.source_name or "Clip").strip() or "Clip"
        if payload.clip_index and payload.clip_total:
            title = f"{base} - clip {payload.clip_index}/{payload.clip_total}"
        else:
            title = base

    try:
        video = await create_video(
            current_user.id,
            url,
            payload.public_id,
            title[:200],
            _clean_targets(payload.upload_targets),
            scraped_from="clipper",
            scheduled_at=when,
        )
    except QuotaExceededError as exc:
        await _discard_asset()
        raise HTTPException(status_code=402, detail=str(exc))
    except Exception as exc:
        await _discard_asset()
        logger.exception("Could not register clip %s", payload.public_id)
        raise HTTPException(status_code=500, detail=f"Could not save the clip: {exc}")

    return {
        "success": True,
        "videoId": video.id,
        "title": video.title,
        "url": url,
        "duration": duration,
        "scheduledAt": when.isoformat() if when else None,
    }
