from datetime import datetime
from io import BytesIO
from googleapiclient.http import MediaIoBaseUpload
import requests
from dotenv import load_dotenv
from controller.mail_controller import trigger_uploaded
from utils import refresh_tiktok_access_token
load_dotenv()
from db import prisma 
from controller.user_credentials import get_tiktok_client, get_youtube_client
import os
import requests



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
        self.upload_target = video.upload_destinations

    async def upload(self):
        for upload_to in self.upload_target:
            if upload_to == "Youtube":
                await upload_to_youtube(self.user,self.video)
            elif upload_to == "Tiktok":
                await upload_to_tiktok(self.user,self.video)
            elif upload_to == "Facebook":
                upload_to_facebook(self.user,self.video)
            elif upload_to == "Instagram":
                upload_to_instagram(self.user,self.video)
            else:
                print(f"Unknown platform: {upload_to}")


async def upload_to_youtube(user,video):


    if video is None:
        print(f"No Pending video is in the list")
        return {"messge":"No video in pending or failed list"}
    youtube = await get_youtube_client(user.credentials) 

    video_url= video.cloudinary_url
    response = requests.get(video_url, stream=True)


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

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": "#shorts #short #fyp #viral #trending #foryou #viralshorts #foryoupage #trendingshorts #viralvideo",
                    "description": (
                        "Discover how the new generation of AI tools is revolutionizing the world! 🚀\n\n"
                        "This video is uploaded by Social Bot, your go-to platform for automating social media tasks.\n"
                        "Join us as we explore the latest advancements in AI technology and how they can help you go viral!\n"
                        "Automate your channel with : https://social-bot.my-port.space\n"
                        "🔍 Featuring: Social Bot, Automation, and Next-Gen AI\n"
                        "🎯 Tags: #AI #ArtificialIntelligence #Shorts #Viral #FYP #Tech #Innovation #Automation\n"
                        "📢 Don't forget to like, comment, and subscribe for more!\n\n\n"
                    ),
                    "tags": [
                        "AI", "Shorts", "Viral", "FYP", "Automation", "Tech", "Artificial Intelligence",
                        "Social Bot", "New Gen AI", "Go Viral", "Trending Shorts", "Machine Learning",
                        "Future Tech", "viralshorts", "foryou", "foryoupage"
                    ] 
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


# Upload to tiktok
async def upload_to_tiktok(user, video):
    # Implement TikTok upload logic here

    tiktok_api_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    client_credentials = get_tiktok_client(user.credentials)
    if client_credentials is None:
        print("No TikTok credentials found.")
        return {"message": "No TikTok credentials found."}
    
    #tiktok access_token will expire in 24 hrs
    refresh_token = client_credentials['refresh_token'] 
    new_access_token = refresh_tiktok_access_token(refresh_token)
 
    headers = {
        "Authorization": f"Bearer {new_access_token}",
        "Content-Type": "application/json; charset=UTF-8"
    }

    # Generate a SEO-based title for TikTok
    seo_title = "#fyp #viral #trending #shorts #viralaccount #growaccount #ai #automation #socialbot Revolutionize your social media with Social Bot! 🚀 Automate video uploads, boost engagement, and go viral effortlessly."

    #Only for tiktok -> We've verified sobot-media-files.my-port.space, If we directly pass video.cloudinray_url will get Error from TIKTOK

    video_file_name = extract_filename_from_url(video.cloudinary_url)
    video_proxy_url = f"https://sobo-media-files.my-port.space/videos/{video_file_name}"

    data = {
        "post_info": {
            "title": seo_title,
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": True,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000
        },
        "source_info": {
            "source": "PULL_FROM_URL",
            "video_url": video_proxy_url
        }
    }

    try:
        response = requests.post(tiktok_api_url, headers=headers, json=data)
        if response.status_code == 200:
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
            # send_mail(user.email, video.cloudinary_url, video.title, "Failed")
            print(f"TikTok upload failed: {response.text}")
    except Exception as e:
        await prisma.video.update(
            where={"id": video.id},
            data={"upload_status": "Failed"}
        )
        send_mail(user.email, video.cloudinary_url, video.title, "Failed")
        print(f"Error uploading to TikTok: {str(e)}")

def upload_to_facebook(user,video):
    # Implement Facebook upload logic here
    return {"message": "Facebook upload not implemented yet."}

def upload_to_instagram(user,video):
    # Implement Instagram upload logic here
    return {"message": "Instagram upload not implemented yet."}

    



      
