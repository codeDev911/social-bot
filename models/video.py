from db import prisma
from lib.plans import PLANS


class QuotaExceededError(Exception):
    """Raised when a user has no free scheduling slots left on their plan."""


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
):
    # Enforced before the row exists, so a full queue can never be exceeded.
    await assert_can_schedule(user_id)

    video = await prisma.video.create(
        data={
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
        },
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
    try:
        await prisma.usage.update(
            where={
                "userId": video.userId
            },
            data={
                "scheduledToday": {
                    "increment": 1
                }
            }
        )
    except Exception:
        # Usage rows are created at signup; a missing row must not lose the video.
        pass
    return video