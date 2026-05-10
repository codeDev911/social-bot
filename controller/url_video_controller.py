import yt_dlp
import cloudinary 
import cloudinary.uploader 
import os
from models.video import create_video
import datetime 
from redis import Redis
from rq import Queue
from wroker import generate_caption_htags

# Setup redis
redis_conn = Redis.from_url("redis://default_ro:AqNxACQgZGE5ZTUxOGEtMDQzZi00NjdjLTk1YjYtOTZlODIyYWU5YmQzr8ysXHUHAEZBtQODSduNaoQZvxvc-N6CjMlVFsDpUww=@fly-contentby-redis.upstash.io:6379")

q = Queue("automates",connection = redis_conn)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)


tempFiles = []

async def upload_to_cloudinary(filePath:str,userId:any,upload_targets:any):
    """This will upload the asset to cloudinary and create video in database will save the details needed"""
    upload_response = cloudinary.uploader.upload(
        filePath,
        resource_type="video",
        folder="social-bot-videos"  # Optional: Specify a folder in Cloudinary
    )

    print("Uploaded video to Cloudinary:", upload_response['secure_url'])
    video = await create_video(userId,upload_response["secure_url"],upload_response["asset_id"],str(datetime.datetime.now()),upload_targets)        
    userPlan = video.user.plan.type


    # Add a worker that generate Caption and #tags for video. use Redis and rq for this
    # This will push the worker to the redis to the first arguments is a function and the rest are the arguments of the generate_caption function
    print("Assigned to redis queue a job")
    q.enqueue(generate_caption_htags,video.id,userPlan,"New viral video","trends")


class FromPlatform: 
    def __init__(self,video_url,user_id:int,upload_targets=["Youtube"]):
        self.user_id = user_id,
        self.video_url =  video_url
        self.upload_targets = upload_targets

    def get_temp_file_name(self):
        FOLDER_PATH = "./tmp"
        return f"{FOLDER_PATH}/{datetime.datetime.now()}.mp4"
    

    async def tiktok(self):  
        filePath = self.get_temp_file_name()
        sanitized_file_path = filePath.replace(" ", "-").replace(":", "-").replace(".","-")
        try:
            ydl_opts = { 
            'format': 'bestvideo+bestaudio/best', 
            'quiet': False,
            "outtmpl": f"{sanitized_file_path}",
            'postprocessors': [],  
            } 
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.video_url])
                print("video downloaded")    
            
            print("userid",self.user_id[0])
            print("upload_targets",self.upload_targets)
            # return {"success":"Video downloaded from tiktok success"}
            # Upload directly to Cloudinary
            await upload_to_cloudinary(sanitized_file_path,self.user_id[0],self.upload_targets)
            os.remove(sanitized_file_path)


            return {"success":f"Video uploaded to Cloudinary success"}

        except Exception as e:
            print(f"Error : {e}") 
            return {"Error":f"{e}"}
        
    async def facebook(self):
        return await self.tiktok()  # Because tiktok and facebook has same video downlaod proccedure

    async def youtube(self):
        return await self.tiktok() # Because tiktok and youtube has same video downlaod proccedure
    
    async def instagram(self):
        return await self.tiktok() # Because tiktok and instagram has same video downlaod proccedure
    
    async def pexels(self):
        print("Pexels video loaded")
        await upload_to_cloudinary(self.video_url,self.user_id[0],self.upload_targets)
        return {"success":f"Video uploaded to Cloudinary success"}
    
    def __getitem__(self, platform):
        methods = {
            "tiktok": self.tiktok,
            "facebook": self.facebook,
            "youtube": self.youtube,
            "instagram": self.instagram,
            "pexels": self.pexels,
        }
        if platform not in methods:
            raise KeyError(f"Platform '{platform}' is not supported.")
        return methods[platform]()
    
