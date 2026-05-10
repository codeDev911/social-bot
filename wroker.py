from db import prisma
from lib.subscribtion_allowed import FeatureChecker


async def generate_caption_htags(video_id:any,userPlan:str,topic:str, tone:str, platform_cfg:any):
    features = FeatureChecker(userPlan) # get features that are allowed in user plan


    print("Generating Captions and H tags")
    captions = features.run_service("caption_gen")(topic,tone, {
        "caption_max": 650, # use platform_cfg if needed
    })

    print("Captions generated!")
    htags = features.run_service("hashtag_gen")(topic ,tone,{
        "hashtags_max": 5, # use platform_cfg if needed
    })
    print("Htags  generated!")

    # Update video and save generated htags and captions
    video = await prisma.video.update(
        where={
            id:video_id
        },
        data={
            captions,
            htags,
        },
        include={
            "user":True
        }
    )
    print("Video update and caption and Htags saved!")


    # update user usage
    await prisma.usage.update(
        where={
            "userId":video.user.id
        },
        data={
            "hTagRequests":{
                "increment":1
            },
            "captionRequests":{
                "increment":1
            },
        }
    )
    print("Usage updated!")

