# plans.py
from controller.mvp_agent.caption_gen import generate_captions
from controller.mvp_agent.hashtag_gen import generate_hashtags
from controller.mvp_agent.time_picker import pick_best_times

PLANS = {
    "starter": {
        "max_upload_per_day": 2,
        "video_image_quality": "sd",
        "is_access_to_pexel_uploads": False,
        "send_mail_on_upload": True,
        "is_allow_access": True,
        "max_account_connection":2,
        "max_scheduled_videos": 8,
        "caption_gen": lambda: generate_captions,
        "hashtag_gen": lambda: generate_hashtags,
        "best_time_picker": lambda: pick_best_times,
        "analytics": False,
    },
    "pro": {
        "max_upload_per_day": 4,
        "video_image_quality": "hd",
        "is_access_to_pexel_uploads": True,
        "send_mail_on_upload": True,
        "is_allow_access": True,
        "max_account_connection":4,
        "max_scheduled_videos": 20,
        "caption_gen": lambda: generate_captions,
        "hashtag_gen": lambda: generate_hashtags,
        "best_time_picker": lambda: pick_best_times,
        "analytics": True,
    }
}
