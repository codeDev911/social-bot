from datetime import datetime
from io import BytesIO
from googleapiclient.http import MediaIoBaseUpload
import requests
from dotenv import load_dotenv
from controller.mail_controller import trigger_uploaded
from utils import refresh_tiktok_access_token
load_dotenv()
from db import prisma 
from controller.user_credentials import get_tiktok_client, get_youtube_client,get_facebook_client,get_instagram_client
import os
from controller.mail_controller import new_video_scheduled_mail

import requests
from controller.buffer import publish_instantly



def send_mail(recipient, file_name, title, video_status):
    trigger_uploaded(
        file_name=file_name,
        title=title,
        size="N/A",
        triggered_at=datetime.utcnow().strftime("%d-%m-%Y %I-%M %p"),
        video_status=video_status,
        recipient=recipient)


class UploadVideo:
    def __init__(self, user,video):
        self.user = user
        self.video = video
        self.upload_target = video.upload_destinations if video.upload_destinations else user.userConfig.defaultUploadDests

    async def upload(self):
        for upload_to in self.upload_target:
            try:
                if upload_to == "Youtube":
                    print("Uploading to Youtube... ")
                    await upload_to_youtube(self.user,self.video)
                elif upload_to == "Tiktok":
                    print("Uploading to Tiktok... ")
                    await upload_to_tiktok(self.user,self.video)
                elif upload_to == "Facebook":
                    print("Uploading to Facebook... ")
                    await upload_to_facebook(self.user,self.video)
                elif upload_to == "Instagram":
                    print("Uploading to Instagram... ")
                    await upload_to_instagram(self.user,self.video)
                else:
                    print(f"Unknown platform: {upload_to}")
            except Exception as e:
                print(f"Error uploading to {upload_to}: {str(e)}")
                continue

async def upload_to_youtube(user,video):


    if video is None:
        print(f"No Pending video is in the list")
        return {"messge":"No video in pending or failed list"}
    youtube = await get_youtube_client(user.credentials) 

    video_url= video.cloudinary_url
    response = requests.get(video_url, stream=True)

    user_config = user.userConfig

    if response.status_code != 200:
        return {"messge":"No video found"}

    print("Streaming video data...")
   # Prepare a file-like object for streaming
    video_stream = BytesIO()
    for chunk in response.iter_content(chunk_size=8192):  # Fetch 8KB chunks
        video_stream.write(chunk)
    
    # Reset stream pointer to the beginning
    video_stream.seek(0)
    
    # Create a media upload object
    media = MediaIoBaseUpload(video_stream, mimetype="video/mp4", chunksize=-1, resumable=True)

    captions = video.captions if video.captions else user_config.defaultCaption
    htags = video.htags if video.htags else user_config.defaultHtags

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": htags,
                    "description":captions,
                },
                "status": {
                    "privacyStatus": "public"
                }
            },
            media_body=media
        )
        response = request.execute()

        await prisma.video.update(
            where={
                "id":video.id
            },
            data={
                "upload_status":"Uploaded",
            }
        )

        await prisma.usage.update(
            where={
                "userId":user.id
            },
            data={
                "uploadsToday":{
                    "increment":1
                }, 
            }
        )
    
        send_mail(user.email, video.cloudinary_url, video.title, "Uploaded")
        print(f"Video uploaded to YouTube: {response['id']}")
    except Exception as e:
        print(f"Error uploading to YouTube: {str(e)}")
        video = await prisma.video.update(
            where={
                "id":video.id
            },
            data={
                "upload_status":"Failed",
            }
        )

        send_mail(user.email, video.cloudinary_url, video.title, "Failed")



# Extract file name from url -> https://res.cloudinary.com/dn....asd/asdads/aasdad/videoname.mp4 -> videoname.mp4
def extract_filename_from_url(url):
        return url.split("/")[-1].split("?")[0]


async def upload_using_buffer(user,video,client_credentials,post_type=None):

    if client_credentials is None:
        print("No  credentials found.")
        return {"status_code": 400, "message": "No  credentials found."}
    
    print("Client credentials found, proceeding with upload...")
    platformId = client_credentials['client_id'] 
    caption = video.captions if video.captions else f"{user.userConfig.defaultCaption} {user.userConfig.defaultHtags}"
    
    try:
        response = await publish_instantly(platformId, caption, video.cloudinary_url, user.userConfig.bufferApiKey, post_type)

        if response["status_code"] == 200:
            await prisma.video.update(
                where={"id": video.id},
                data={"upload_status": "Uploaded"}
            )
            send_mail(user.email, video.cloudinary_url, video.title, "Uploaded")

            print("Video uploaded to TikTok.")
        else:
            await prisma.video.update(
                where={"id": video.id},
                data={"upload_status": "Failed"}
            )
            send_mail(user.email, video.cloudinary_url, video.title, "Failed")
            print(f"Buffer upload failed: {response.get('error')}")
        return response
    except Exception as e:
        error_response = {"status_code": 500, "error": str(e)}
        await prisma.video.update(
            where={"id": video.id},
            data={"upload_status": "Failed"}
        )
        send_mail(user.email, video.cloudinary_url, video.title, "Failed")
        print(f"Error uploading: {str(e)}")
        return error_response
    finally:
         await prisma.usage.update(
            where={
                "userId":user.id
            },
            data={
                "uploadsToday":{
                    "increment":1
                }, 
            }
        )

# Upload to tiktok
async def upload_to_tiktok(user, video):
    client_credentials = get_tiktok_client(user.credentials)
    return await upload_using_buffer(user,video,client_credentials)

async def upload_to_facebook(user, video):
    client_credentials = get_facebook_client(user.credentials)
    return await upload_using_buffer(user, video, client_credentials, post_type="post") # or "reel" based on your requirement


async def upload_to_instagram(user,video):
    client_credentials = get_instagram_client(user.credentials)
    return await upload_using_buffer(user,video,client_credentials)

    



      
