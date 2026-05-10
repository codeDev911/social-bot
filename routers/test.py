import json
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.templating import Jinja2Templates
import httpx
import os
from dotenv import load_dotenv
from fastapi.responses import   StreamingResponse
from urllib.parse import unquote

from datetime import datetime

from db import prisma
from lib.depends import get_current_user
from lib.subscribtion_allowed import FeatureChecker
load_dotenv()  # Load .env file

router = APIRouter(prefix="/test")

PEXEL_API_KEY = os.getenv("PEXEL_API_KEY")
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_POPULAR_VIDEOS_URL = "https://api.pexels.com/videos/popular"

headers = {
    "Authorization": PEXEL_API_KEY
}

@router.get("/")
def test():
    urls = [
        {
            "Popular videos":"/test/videos/popular"
        },
        {
            "Search videos":"/test/videos/search"
        },
    ]
    return {"test api urls":urls}

templates = Jinja2Templates(directory="templates")

@router.get("/html")
def testHtml(request:Request):
     return templates.TemplateResponse(
            "test.html",{
            "request": request, 
            } 
     )

# Example: Assume we fetch plan from DB
def get_user_plan():
    return "pro"

@router.get("/plan")
async def subscribe_plan():
    plan = await prisma.user.update(
        where={
            "email":"afnanahhad@gmail.com"
        },
        data={
            "plan":{
                "create":{
                    "name":"Starter",
                    "type":"starter",
                    "trailExpiredAt":datetime.now().replace(year=datetime.now().month + 1),
                    "subscribtion":json.dumps({"paddle_plan_id":12345,"paddle_subscription_id":67890,"status":"active"})
                }
            }
        }
    )

    return plan

@router.get("/subscription-check")
def upload_video(uploads_today: int, current_user: str = Depends(get_current_user)):
    if isinstance(current_user, Response):
        return current_user
    
    userPlan = current_user.plan.type if current_user.plan else "free"
    plan = FeatureChecker(userPlan)

    # check upload limit
    plan.can_upload(uploads_today) 
    # run caption generation if allowed
    caption = plan.run_service("caption_gen")("test topic", "funny", {
        "caption_max": 2200,
        "hashtags_max": 30,
        "time_windows_local": [
        ["12:30", "14:00"],
        ["18:00", "22:00"]
        ]
    })
    best_time = plan.run_service("best_time_picker")( {
        "caption_max": 2200,
        "hashtags_max": 30,
        "time_windows_local": [
        ["12:30", "14:00"],
        ["18:00", "22:00"]
        ]
    })
    return {"status": "uploaded", "caption": caption,"best_time": best_time}

@router.get("/video-proxy")
async def proxy_video(url: str):
    # Decode the URL if it's URL-encoded
    video_url = unquote(url)

    # Stream the content using HTTPX
    async with httpx.AsyncClient() as client:
        response = await client.get(video_url, follow_redirects=True)
        if response.status_code != 200:
            return Response("Failed to fetch video", status_code=502)

        content_type = response.headers.get("Content-Type", "video/mp4")

        return StreamingResponse(
            iter([response.content]),
            media_type=content_type,
            headers={
                "Content-Disposition": "inline",  # This is what prevents download
                "Content-Type": content_type,
            },
        )

@router.get("/videos/popular") 
async def get_popular_videos(request:Request,per_page: int = 10):

    try:
        # if can_   access_premium(request,None) is not True:
        #      return can_access_premium(request,None)

        async with httpx.AsyncClient() as client: 
            response = await client.get(PEXELS_POPULAR_VIDEOS_URL, headers=headers, params={"per_page": per_page})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return Response(e,500)


@router.get("/videos/search")
async def search_videos(query: str = Query(..., min_length=1), per_page: int = 10):
    async with httpx.AsyncClient() as client:
        response = await client.get(PEXELS_VIDEO_SEARCH_URL, headers=headers, params={"query": query, "per_page": per_page})
        response.raise_for_status()
        return response.json()
