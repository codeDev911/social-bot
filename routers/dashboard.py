from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from controller.pexels import UploadPexelVideo
from controller.url_video_controller import FromPlatform
from controller.video_uploader import  UploadVideo
from models.video import create_video
from db import prisma
from lib.depends import get_current_user
from fastapi.exceptions import HTTPException
import cloudinary
import cloudinary.uploader
from utils import validate_video_url,verify_upload_target
from controller.delete_uploaded_files import delete_uploaded_files
from dotenv import load_dotenv
import os
load_dotenv()

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/dashboard")

INTERNAL_SECRET=os.getenv("INTERNAL_SECRET")


def _normalize_credentials(credentials):
    if not credentials:
        return {}
    return credentials if isinstance(credentials, dict) else {}


def _build_dashboard_context(request: Request, current_user, *, videos=None, total_videos_count=0, pending_videos_count=0, canceled_videos_count=0):
    user_info = current_user
    credentials = _normalize_credentials(getattr(user_info, "credentials", None))
    plan = getattr(user_info, "plan", None)
    usage = getattr(user_info, "usage", None)

    plan_name = (getattr(plan, "name", None) or "Free").strip()
    plan_type = getattr(plan, "type", None)
    plan_limits = {
        "starter": {"videos": 8, "accounts": 2, "ai_features": "Basic"},
        "pro": {"videos": 20, "accounts": 4, "ai_features": "Advanced"},
        "free": {"videos": 2, "accounts": 1, "ai_features": "Basic"},
    }
    plan_key = str(plan_type or plan_name).lower()
    limits = plan_limits.get(plan_key, plan_limits["free"])
    today_uploads = getattr(usage, "uploadsToday", 0) if usage else 0
    scheduled_videos = getattr(usage, "scheduledToday", 0) if usage else 0
    caption_generations = getattr(usage, "captionRequests", 0) if usage else 0
    hashtag_generations = getattr(usage, "hTagRequests", 0) if usage else 0
    ai_processing_used = caption_generations + hashtag_generations
    ai_processing_limit = 1000

    def pct(value, limit):
        if not limit:
            return "0"
        return str(round((value / limit) * 100))

    return {
        "request": request,
        "user_info": user_info,
        "user_id": getattr(user_info, "id", None),
        "connected_platforms": credentials,
        "title": "Home Page",
        "videos": videos or [],
        "total_videos_count": total_videos_count,
        "pending_videos_count": pending_videos_count,
        "canceled_videos_count": canceled_videos_count,
        "uploaded_videos_count": sum(1 for video in (videos or []) if getattr(video, "upload_status", None) == "Uploaded"),
        "processing_videos_count": sum(1 for video in (videos or []) if getattr(video, "upload_status", None) == "Processing"),
        "today_uploads": today_uploads,
        "scheduled_videos": scheduled_videos,
        "caption_generations": caption_generations,
        "hashtag_generations": hashtag_generations,
        "ai_processing_used": ai_processing_used,
        "ai_processing_limit": ai_processing_limit,
        "caption_ai_usage": caption_generations,
        "hashtag_ai_usage": hashtag_generations,
        "today_uploads_pct": pct(today_uploads, 8),
        "scheduled_videos_pct": pct(scheduled_videos, 20),
        "caption_generations_pct": pct(caption_generations, 100),
        "hashtag_generations_pct": pct(hashtag_generations, 100),
        "ai_processing_pct": pct(ai_processing_used, ai_processing_limit),
        "current_plan": plan_name,
        "plan_price": "$8" if plan_key == "starter" else "$16" if plan_key == "pro" else "$0",
        "plan_video_limit": limits["videos"],
        "plan_account_limit": limits["accounts"],
        "plan_ai_features": limits["ai_features"],
        "next_billing": getattr(plan, "trailExpiredAt", None),
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user  
    try: 
        
        videos  = await prisma.video.find_many(
            where={
                "userId":current_user.id
            },
            order={
                 "updatedAt":"desc"   
            },
            take=20
        )
        total_videos_count = await prisma.video.count(
            where={
            "userId": current_user.id
            }
        )

        pending_videos_count = await prisma.video.count(
            where={
            "userId": current_user.id,
            "upload_status": "Pending"
            }
        )
        canceled_videos_count = await prisma.video.count(
            where={
            "userId": current_user.id,
            "upload_status": "Failed"
            }
        )
 

        user_info = await prisma.user.find_unique(
            where={
                "id": current_user.id
            },
            include={
                "plan": True,
                "usage": True,
            },
    )
  

        return templates.TemplateResponse(
            "dashboard/index.html",
            _build_dashboard_context(
                request,
                user_info,
                videos=videos,
                total_videos_count=total_videos_count,
                pending_videos_count=pending_videos_count,
                canceled_videos_count=canceled_videos_count,
            ),
        )

    except HTTPException as e:
        return templates.TemplateResponse("500.html", {"request": request, "title": "Home Page","error":e})

@router.get("/videos", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user 
    videos = await prisma.video.find_many(
        where={"userId": current_user.id},
        order={"updatedAt": "desc"},
        take=50,
    )
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    total_videos_count = await prisma.video.count(where={"userId": current_user.id})
    pending_videos_count = await prisma.video.count(where={"userId": current_user.id, "upload_status": "Pending"})
    canceled_videos_count = await prisma.video.count(where={"userId": current_user.id, "upload_status": "Failed"})
    return templates.TemplateResponse(
        "dashboard/videos.html",
        _build_dashboard_context(
            request,
            user_info,
            videos=videos,
            total_videos_count=total_videos_count,
            pending_videos_count=pending_videos_count,
            canceled_videos_count=canceled_videos_count,
        ),
    )

@router.get("/settings", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user 
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    if user_info is None:
        raise HTTPException(status_code=404, detail="User not found")
    return templates.TemplateResponse(
        "dashboard/settings.html",
        _build_dashboard_context(request, user_info),
    )

@router.get("/plan", response_class=HTMLResponse)
async def dashboard_home(request:Request,current_user: dict = Depends(get_current_user)):
    if isinstance(current_user, RedirectResponse):
        return current_user 
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    return templates.TemplateResponse(
        "dashboard/plan.html",
        _build_dashboard_context(request, user_info),
    )


@router.post("/upload")    
async def upload_video(
    file: UploadFile = File(...), 
    current_user : dict = Depends(get_current_user),
    upload_destinations: list = Depends(verify_upload_target)):

    if isinstance(current_user, RedirectResponse):
        return current_user   
    
    if not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")
    try:
        # Upload to Cloudinary
        result = cloudinary.uploader.upload(file.file, resource_type="video" ,folder="social-bot-videos")
        await create_video(current_user.id,result["secure_url"],result["asset_id"],file.filename,upload_destinations)

        return Response(content={"message":"Video uploaded success!"},status_code=201)
        
         
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


# Upload video file from Url across Tiktok,youtube and facebook 
@router.post("/upload-from-url")
async def upload_url_video(
    platform: str = Depends(validate_video_url),
    url: str = Query(...),
    upload_destinations: list = Depends(verify_upload_target),
    background_tasks: BackgroundTasks = BackgroundTasks()
    ,current_user: dict = Depends(get_current_user)
    ):
  
    if isinstance(current_user,RedirectResponse):
        return current_user
    
    # Calling a class that has many Facebook, Tiktok, Youtube and Instagram video download and uploader to Cloudinary
    down_and_upload_from = FromPlatform(video_url=url,user_id=current_user.id,upload_targets=upload_destinations)
    background_tasks.add_task(await down_and_upload_from[platform])
    return {"message":"Process started"}


# This is scheduled by DBOS
# Upload pexels random video
@router.get("/trigger-pexels-upload")
async def trigger_pexels_upload(background_tasks:BackgroundTasks,per_page : int = 5):
    return await UploadPexelVideo(background_tasks,per_page)



# This is Scheduled by DBOS 
@router.post("/trigger-upload")
async def trigger_youtube_upload(background_tasks: BackgroundTasks,user_id:int,secret:str):

    if secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    uploaded_users=[] # add uploaded video user id

    pending_videos = await prisma.video.find_many(
        where={
            "upload_status":"Pending",
            "AND":[
                {"upload_status":"Pending"},
                {"userId":user_id}
            ]
        },
        order={
            "created_at":"asc"
        },
         include={
             'user':{
                "include":{
                    "userConfig":True
                }
             }
         }
    )

    for video in pending_videos:
        if not video.user.id in  uploaded_users: # Check only one video of a user will upload dailyl
            background_tasks.add_task(await UploadVideo(video.user,video).upload())
            # Added this to background task to upload without waiting for the response
            uploaded_users.append(video.user.id)
    return {"message": "upload process triggered"}

 

# This is be trigger by schedule on DBOS
@router.post("/trigger-deleted-uploaded-files")
async def trigger_delete_uploaded_files(background_tasks: BackgroundTasks):
    background_tasks.add_task(await delete_uploaded_files())
    print("Delete uploaded files triggered")
    return {"message":"Delete uploaded files triggered"}
