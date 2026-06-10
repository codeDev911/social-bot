import yt_dlp
import cloudinary 
import cloudinary.uploader 
from dotenv import load_dotenv
import os
from models.video import create_video
import datetime 
from redis import Redis
from rq import Queue
from wroker import generate_caption_htags
from db import prisma
from imageio_ffmpeg import get_ffmpeg_exe
from controller.copyright_transformer import build_transformations_array
import json
load_dotenv()
# Setup redis
redis_conn = Redis.from_url("redis://default_ro:AqNxACQgZGE5ZTUxOGEtMDQzZi00NjdjLTk1YjYtOTZlODIyYWU5YmQzr8ysXHUHAEZBtQODSduNaoQZvxvc-N6CjMlVFsDpUww=@fly-contentby-redis.upstash.io:6379")

q = Queue("automates",connection = redis_conn)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)


tempFiles = []


def deserialize_and_map_dynamic_edits(dynamic_edits_config):
    """
    Deserialize JSON strings from database and map frontend field names to transformer field names.
    Frontend sends: {speed, frequency, mute, text, color, opacity, position, yGap, vignette, noise}
    Transformer expects: {audio_speed, audio_frequency, mute_audio, text_content, text_color, text_opacity, text_gravity, text_y_gap, vignette_strength, noise_strength}
    """
    if not dynamic_edits_config:
        return None
    
    try:
        # Deserialize JSON strings from database
        audio_controls = dynamic_edits_config.get("audioControls", {})
        if isinstance(audio_controls, str):
            audio_controls = json.loads(audio_controls)
        
        text_overlay_controls = dynamic_edits_config.get("textOverlayControls", {})
        if isinstance(text_overlay_controls, str):
            text_overlay_controls = json.loads(text_overlay_controls)
        
        pixel_distortion_controls = dynamic_edits_config.get("pixelDistortionControls", {})
        if isinstance(pixel_distortion_controls, str):
            pixel_distortion_controls = json.loads(pixel_distortion_controls)
        
        # Map frontend field names to transformer field names
        mapped_config = {
            "audioControls": {
                "audio_speed": audio_controls.get("speed"),
                "audio_frequency": audio_controls.get("frequency"),
                "mute_audio": audio_controls.get("mute", False)
            },
            "textOverlayControls": {
                "text_content": text_overlay_controls.get("text", ""),
                "text_color": text_overlay_controls.get("color", "#F97316"),
                "text_opacity": text_overlay_controls.get("opacity", 60),
                "text_gravity": text_overlay_controls.get("position", "south"),
                "text_y_gap": text_overlay_controls.get("yGap", 120)
            },
            "pixelDistortionControls": {
                "vignette_strength": pixel_distortion_controls.get("vignette", 0),
                "noise_strength": pixel_distortion_controls.get("noise", 0)
            }
        }
        
        return mapped_config
    except Exception as e:
        print(f"Error deserializing dynamic edits: {str(e)}")
        return None



async def upload_to_cloudinary(filePath:str,userId:any,upload_targets:any,video_id=None):
    """This will upload the asset to cloudinary and create video in database will save the details needed"""
    
    # Fetch user's dynamic edit configuration
    user = await prisma.user.find_unique(
        where={"id": userId},
        include={"dynamicEdits": True}
    )
    
    # Get dynamic edits config if exists
    dynamic_edits_config = None
    if user and user.dynamicEdits:
        raw_config = {
            "audioControls": user.dynamicEdits.audioControls,
            "textOverlayControls": user.dynamicEdits.textOverlayControls,
            "pixelDistortionControls": user.dynamicEdits.pixelDistortionControls,
        }
        # Deserialize and map field names
        dynamic_edits_config = deserialize_and_map_dynamic_edits(raw_config)
    
    # Build transformations from user config or use defaults
    transformations_list = build_transformations_array(userId, dynamic_edits_config)
    
    upload_response = cloudinary.uploader.upload(
        filePath,
        resource_type="video",
        transformation = transformations_list,
        folder="social-bot-videos"  # Optional: Specify a folder in Cloudinary
    )

    print("Uploaded video to Cloudinary:", upload_response['secure_url'])
    video = await create_video(userId,upload_response["secure_url"],upload_response["asset_id"],str(datetime.datetime.now()),upload_targets,video_id)        
    userPlan = video.user.plan.type


    # Add a worker that generate Caption and #tags for video. use Redis and rq for this
    # This will push the worker to the redis to the first arguments is a function and the rest are the arguments of the generate_caption function
    print("Assigned to redis queue a job")
    q.enqueue(generate_caption_htags,video.id,userPlan,"New viral video","trends")



PROXY_USER = os.getenv("WEBSHARE_USERNAME")
PROXY_PASS = os.getenv("WEBSHARE_PASSWORD")
PROXY_HOST = "p.webshare.io"  
PROXY_PORT = "80"    

qjs_path = os.path.join(os.getcwd(), "bin", "qjs")



class FromPlatform: 
    def __init__(self,video_url,user_id:int,upload_targets=["Youtube"],video_id=None):
        self.user_id = user_id,
        self.video_url =  video_url
        self.video_id =  video_id
        self.upload_targets = upload_targets
        self.proxy_url =  f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"


    def get_temp_file_name(self):
        FOLDER_PATH = "/tmp"
        return f"{FOLDER_PATH}/{datetime.datetime.now()}.mp4"

    async def tiktok(self):  
        filePath = self.get_temp_file_name()
        sanitized_file_path = filePath.replace(" ", "-").replace(":", "-")
        ffmpeg_binary_path = get_ffmpeg_exe()
        try:
            ydl_opts = { 
            'format': 'b[ext=mp4]', # 'b' forces a single pre-merged video+audio file (bypasses heavy merging)
            # 'format': 'bestvideo+bestaudio/best', # high-quality 1080p/4K
            #  'proxy': self.proxy_url,  # TikTok often blocks requests from certain IPs, using a proxy can help bypass this
            'ffmpeg_location': ffmpeg_binary_path,
            'quiet': True,
            "outtmpl": f"{sanitized_file_path}",
            'postprocessors': [],  
            'merge_output_format': 'mp4',
            } 

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.video_url])
                print("video downloaded")    
            
            print("userid",self.user_id[0])
            print("upload_targets",self.upload_targets) 

            await upload_to_cloudinary(sanitized_file_path,self.user_id[0],self.upload_targets,self.video_id)

            await prisma.usage.update(
                where={
                    "userId":self.user_id[0]
                },
                data={
                    "scheduledToday":{
                        "increment":1
                    }, 
                }
            )
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
    
