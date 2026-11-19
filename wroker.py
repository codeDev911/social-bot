import asyncio
from db import prisma
from lib.subscribtion_allowed import FeatureChecker

async def generate_caption_htags(video_id, userPlan, topic, tone, platform_cfg):
    features = FeatureChecker(userPlan)

    print("Generating Captions and H tags")

    # The Gemini SDK calls are synchronous and would block the event loop.
    caption_generator = features.run_service("caption_gen")
    captions = await asyncio.to_thread(
        caption_generator, topic, tone, {"caption_max": 650}
    )

    hashtag_generator = features.run_service("hashtag_gen")
    htags = await asyncio.to_thread(
        hashtag_generator, topic, tone, {"hashtags_max": 5}
    )

    video = await prisma.video.update(
        where={"id": video_id},
        data={
            "captions": captions,
            "htags": htags,
        },
        include={"user": True}
    )

    await prisma.usage.update(
        where={"userId": video.user.id},
        data={
            "hTagRequests": {"increment": 1},
            "captionRequests": {"increment": 1},
        }
    )

    print("Done")


