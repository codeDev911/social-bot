from datetime import datetime

from db import prisma
from lib.plans import PLANS


class QuotaExceededError(Exception):
    """Raised when a user has no free scheduling slots left on their plan."""


# Placeholder rows created when a job is handed to the download worker carry
# "job:<worker job id>" in upload_id: the dashboard follows live progress
# through it and the completion webhook updates that row instead of inserting
# a second one. Cleared as soon as the job reports back.
QUEUED_PREFIX = "job:"


def queued_marker(job_id: str) -> str:
    return f"{QUEUED_PREFIX}{job_id}"


def queued_job_id(video) -> str | None:
    value = getattr(video, "upload_id", "") or ""
    return value[len(QUEUED_PREFIX):] if value.startswith(QUEUED_PREFIX) else None


async def _increment_scheduled(user_id: int) -> None:
    # Single source of truth for the scheduled counter: callers must not
    # increment scheduledToday themselves.
    try:
        await prisma.usage.update(
            where={"userId": user_id},
            data={"scheduledToday": {"increment": 1}},
        )
    except Exception:
        # Usage rows are created at signup; a missing row must not lose the video.
        pass


async def _resolve_plan_key(user_id: int) -> str:
    """Return the user's plan key, falling back to 'free' when unknown."""
    user = await prisma.user.find_unique(where={"id": user_id}, include={"plan": True})
    plan = user.plan if user else None
    plan_type = getattr(plan.type, "value", plan.type) if plan else None
    return plan_type if plan_type in PLANS else "free"


async def assert_can_schedule(user_id: int, slots: int = 1) -> int:
    """
    Ensure the plan has room for `slots` more scheduled (Pending) videos.

    Returns the current number of pending videos. Raises QuotaExceededError when
    the limit would be exceeded so callers can bail out before downloading and
    uploading anything to Cloudinary.
    """
    plan_key = await _resolve_plan_key(user_id)
    max_scheduled = PLANS[plan_key]["max_scheduled_videos"]

    pending = await prisma.video.count(
        where={"userId": user_id, "upload_status": "Pending"}
    )

    if pending + slots > max_scheduled:
        raise QuotaExceededError(
            f"Plan '{plan_key}' allows {max_scheduled} scheduled videos "
            f"and {pending} are already pending"
        )
    return pending


async def create_video(
    user_id: int,
    secure_url: str,
    asset_id: str,
    title: str,
    upload_destinations: any = ["Youtube"],
    video_id=None,
    scraped_from: str | None = None,
    scheduled_at: datetime | None = None,
):
    # Enforced before the row exists, so a full queue can never be exceeded.
    await assert_can_schedule(user_id)

    data = {
        "user": {
            "connect": {
                "id": user_id
            }
        },
        "cloudinary_url": secure_url,
        "cloudinary_id": asset_id,
        "title": title,
        "upload_destinations": upload_destinations,
        "upload_id": "",
        "upload_status": "Pending",
        "externalId": video_id,
        "scrapedFrom": scraped_from or "unkown",
    }
    # The publisher only picks rows whose scheduled_at has arrived, so a row
    # without a date should publish immediately.
    if scheduled_at is not None:
        data["scheduled_at"] = scheduled_at

    video = await prisma.video.create(
        data=data,
        include={
            "user": {
               "include": {
                   "plan": True
               }
            }
        }
    )

    # Single source of truth for the scheduled counter: callers must not
    # increment scheduledToday themselves.
    await _increment_scheduled(video.userId)
    return video


async def create_queued_video(
    user_id: int,
    job_id: str,
    title: str,
    upload_destinations: any = ["Youtube"],
    platform: str | None = None,
):
    """Create the Pending row the dashboard shows while the worker downloads.

    Counted against the plan immediately, so a queue slot cannot be double
    booked and a failed job shows up as Failed instead of vanishing.
    """
    await assert_can_schedule(user_id)

    video = await prisma.video.create(
        data={
            "user": {"connect": {"id": user_id}},
            "cloudinary_url": "",
            "cloudinary_id": "",
            "title": title or "Queued video",
            "upload_destinations": upload_destinations or ["Youtube"],
            "upload_id": queued_marker(job_id),
            "upload_status": "Pending",
            "scrapedFrom": platform or "unkown",
        }
    )
    await _increment_scheduled(user_id)
    return video


async def find_queued_video(user_id: int, job_id: str | None):
    """Return the placeholder row for a worker job, if one was created."""
    if not job_id:
        return None
    return await prisma.video.find_first(
        where={"userId": user_id, "upload_id": queued_marker(job_id)}
    )


async def complete_queued_video(
    video,
    secure_url: str,
    asset_id: str,
    title: str | None = None,
    external_id: str | None = None,
    platform: str | None = None,
):
    """Fill the placeholder in place, so one job maps to exactly one row."""
    return await prisma.video.update(
        where={"id": video.id},
        data={
            "cloudinary_url": secure_url,
            "cloudinary_id": asset_id,
            "title": title or video.title,
            "upload_id": "",
            "upload_status": "Pending",
            "externalId": external_id or video.externalId,
            "scrapedFrom": platform or video.scrapedFrom,
        },
    )


async def fail_queued_video(video):
    """Mark the placeholder Failed and free the marker so it stops polling."""
    return await prisma.video.update(
        where={"id": video.id},
        data={"upload_id": "", "upload_status": "Failed"},
    )