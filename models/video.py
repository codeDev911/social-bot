from db import prisma

async def create_video(user_id:int,secure_url:str,asset_id:str,title:str,upload_destinations:any=["Youtube"],video_id=None):
    video = await prisma.video.create(
        data={
            "user":{
                "connect":{
                    "id":user_id
                }
            },
            "cloudinary_url":secure_url,
            "cloudinary_id":asset_id,
            "title":title,
            "upload_destinations":upload_destinations,
            "upload_id":"",
            "upload_status":"Pending",
            "externalId": video_id
        },
        include={
            "user":{
               "include":{
                   "plan":True
               }
            }
        }
    ) 
    
    # Add a video scheduled usage
    await prisma.usage.update(
        where={
            "userId":video.userId
        },
        data={
            "scheduledToday":{
                "increment": 1
            }
        }
    )
    return video