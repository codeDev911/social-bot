# plans.py
from controller.mvp_agent.caption_gen import generate_captions
from controller.mvp_agent.hashtag_gen import generate_hashtags
from controller.mvp_agent.time_picker import pick_best_times

PLANS = {
    "free": {
        "max_upload_per_day": 2,
        "video_image_quality": "sd",
        "is_access_to_pexel_uploads": False,
        "send_mail_on_upload": True,
        "is_allow_access": True,
        "max_account_connection": 2,
        "max_scheduled_videos": 8,
        "caption_gen": lambda: generate_captions,
        "hashtag_gen": lambda: generate_hashtags,
        "best_time_picker": lambda: pick_best_times,
        "analytics": False,
        # Playground defaults and the Smart Clipper are open to every plan for
        # now; the scheduled-video quota is what actually separates the plans.
        "smart_clipper": True,
        "max_clips_per_run": 13,
        "max_clip_source_minutes": 30,
        "max_clip_seconds": 45,
    },
    "starter": {
        "max_upload_per_day": 4,
        "video_image_quality": "hd",
        "is_access_to_pexel_uploads": True,
        "send_mail_on_upload": True,
        "is_allow_access": True,
        "max_account_connection": 4,
        "max_scheduled_videos": 20,
        "caption_gen": lambda: generate_captions,
        "hashtag_gen": lambda: generate_hashtags,
        "best_time_picker": lambda: pick_best_times,
        "analytics": True,
        "smart_clipper": True,
        "max_clips_per_run": 13,
        "max_clip_source_minutes": 30,
        "max_clip_seconds": 45,
    },
    "pro": {
        "max_upload_per_day": 8,
        "video_image_quality": "hd",
        "is_access_to_pexel_uploads": True,
        "send_mail_on_upload": True,
        "is_allow_access": True,
        "max_account_connection": 6,
        "max_scheduled_videos": 40,
        "caption_gen": lambda: generate_captions,
        "hashtag_gen": lambda: generate_hashtags,
        "best_time_picker": lambda: pick_best_times,
        "analytics": True,
        "smart_clipper": True,
        "max_clips_per_run": 13,
        "max_clip_source_minutes": 30,
        "max_clip_seconds": 45,
    },
}


def plan_limits(plan_key: str) -> dict:
    """Limits for a plan key, falling back to free so callers never KeyError."""
    return PLANS.get((plan_key or "free").lower(), PLANS["free"])


def clipper_config(plan_key: str) -> dict:
    """The shape the clipper page and its upload endpoint both read."""
    limits = plan_limits(plan_key)
    return {
        "plan": plan_key,
        "enabled": bool(limits["smart_clipper"]),
        "maxClips": limits["max_clips_per_run"],
        "maxMinutes": limits["max_clip_source_minutes"],
        "maxClipSeconds": limits["max_clip_seconds"],
    }