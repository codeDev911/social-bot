import json
import os
from dotenv import load_dotenv
from fastapi import Response
import httpx
import random
from controller.video_uploader import UploadVideo
from db import prisma
from models.video import create_video, QuotaExceededError

load_dotenv()

PEXEL_API_KEY = os.getenv("PEXEL_API_KEY")
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_POPULAR_VIDEOS_URL = "https://api.pexels.com/videos/popular"

headers = {
    "Authorization": PEXEL_API_KEY
}



async def UploadPexelVideo(background_tasks,per_page : int = 10):
     # Only for specfic users for now
    users = await prisma.user.find_many(
        where={
            "email":{
                "contains":"afnanahhad2.0"
            }
        }
    )

    pex_res = await getPopularPexelsVideos(per_page)

    if(pex_res) is  None:
        print("Not pex videos")
        # return {"Error":"No pexels videos fetched"}
        return Response(content=json.dumps({"error":"No pexels video fetched"}),status_code=400)

    for user in users:
        # Find the random one 
        random_video = getRandomPexelVideo(pex_res) 
        if random_video is None:
            print("No suitable random video found.")
            continue
        secure_url = random_video.get("link")
        asset_id = random_video.get("id")

        # Save video details in Database (create_video enforces the plan quota)
        try:
            video = await create_video(
                user.id,
                str(secure_url),
                str(asset_id),
                f"PEX_VID{random.randint(0, 999999)}",
                ["Youtube"],
                scraped_from="pexels",
            )
        except QuotaExceededError as e:
            print(f"Skipping user {user.id}: {e}")
            continue

        # Schedule the coroutine function, not an awaited result
        background_tasks.add_task(UploadVideo(user,video).upload)
        return Response(content=json.dumps({"Success":"Video has been uploaded."}),status_code=201)



async def getPopularPexelsVideos(range:int = 10) -> None | dict:
    try:
        
        async with httpx.AsyncClient(timeout=30.0) as client: 
            response = await client.get(PEXELS_POPULAR_VIDEOS_URL, headers=headers, params={"per_page": range})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print({"Error occured while fetching pexel videos":e})
        return None
    
def getRandomPexelVideo(videos):
    if not videos or "videos" not in videos or not videos["videos"]:
        return None

    video = random.choice(videos["videos"])
    # Prefer uhd > hd > sd
    quality_order = {"uhd": 3, "hd": 2, "sd": 1}
    best_file = max(
        video.get("video_files", []),
        key=lambda f: quality_order.get(f.get("quality", ""), 0),
        default=None
    )
    if not best_file:
        return None

    # Check for vertical/shorts format (height > width)
    if best_file["height"] > best_file["width"]:
        return {
                "id": best_file.get("id"),
                "quality": best_file.get("quality"),
                "file_type": best_file.get("file_type"),
                "width": best_file.get("width"),
                "height": best_file.get("height"),
                "fps": best_file.get("fps"),
                "link": best_file.get("link")
        }
    return None