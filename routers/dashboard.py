from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, Response, UploadFile,Header
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from controller.pexels import UploadPexelVideo
from controller.url_video_controller import FromPlatform
from controller.video_uploader import  UploadVideo
from controller.cloudinary_transformer import CloudinaryVideoTransformer, build_playground_transformation
from models.video import create_video
from db import prisma
from lib.depends import get_current_user
from fastapi.exceptions import HTTPException
import cloudinary
import cloudinary.uploader
from utils import validate_video_url,verify_upload_target
from controller.delete_uploaded_files import delete_uploaded_files
from controller.buffer import get_profiles_graphql
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from controller.auth import set_user_credentials
from controller.mail_controller import new_video_scheduled_mail
import os
import json
import secrets
import hashlib
import uuid
from datetime import datetime
import logging

load_dotenv()

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/dashboard")

INTERNAL_SECRET=os.getenv("INTERNAL_SECRET")


class BufferConnectRequest(BaseModel):
    bufferApiKey: str

class GenerateApiKeyRequest(BaseModel):
    name: str = "Discovery Service"

class DynamicEditRequest(BaseModel):
    audioControls: dict = {}
    textOverlayControls: dict = {}
    pixelDistortionControls: dict = {}


class PlaygroundConfigRequest(BaseModel):
    """Playground video transformation configuration (UI preview only)"""
    textOverlay: dict = {
        "enabled": False,
        "text": "@mybrand",
        "fontSize": 48,
        "color": "#F97316",
        "opacity": 100,
        "position": "center"
    }
    audioControls: dict = {
        "volume": 0,
        "frequency": "",
        "audioCodec": "aac",
        "bitrate": "128k",
        "removeAudio": False
    }
    resize: dict = {
        "cropMode": "none",
        "width": None,
        "height": None
    }
    trim: dict = {
        "startOffset": 0,
        "duration": 30
    }
    gravity: dict = {
        "gravity": "auto"
    }
    quality: dict = {
        "quality": "auto"
    }


class PlaygroundTransformationRequest(BaseModel):
    """Playground video transformation configuration"""
    video_url: str
    overlay_type: str = "none"
    overlay_text: str = ""
    font_size: int = 36
    text_color: str = "#F97316"
    text_opacity: int = 100
    crop_mode: str = "none"
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = ""
    start_offset: Optional[float] = None
    end_offset: Optional[float] = None
    duration: Optional[float] = None
    gravity: str = ""
    x_offset: int = 0
    y_offset: int = 0
    auto_crop_type: str = "none"
    quality: str = ""
    dpr: str = ""


class UpdateVideoRequest(BaseModel):
    """Request model for updating video details"""
    title: Optional[str] = None
    captions: Optional[str] = None
    upload_destinations: Optional[list] = None


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
            take=10
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
  
        print(user_info.credentials)

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


@router.get("/playground", response_class=HTMLResponse)
async def video_playground(request:Request, current_user: dict = Depends(get_current_user)):
    """Interactive video transformation playground using Cloudinary API"""
    if isinstance(current_user, RedirectResponse):
        return current_user 
    user_info = await prisma.user.find_unique(
        where={"id": current_user.id},
        include={"plan": True, "usage": True},
    )
    return templates.TemplateResponse(
        "dashboard/playground.html",
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
    print("this hits")
    # Calling a class that has many Facebook, Tiktok, Youtube and Instagram video download and uploader to Cloudinary
    down_and_upload_from = FromPlatform(video_url=url,user_id=current_user.id,upload_targets=upload_destinations)
    print("included")
    background_tasks.add_task(await down_and_upload_from[platform])
    return {"message":"Process started"}


# This is scheduled by DBOS
# Upload pexels random video
@router.get("/trigger-pexels-upload")
async def trigger_pexels_upload(background_tasks:BackgroundTasks,per_page : int = 5):
    return await UploadPexelVideo(background_tasks,per_page)



# This is Scheduled by DBOS 
@router.post("/trigger-upload")
async def trigger_upload(background_tasks: BackgroundTasks,user_id:int,secret:str):

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

    videos_of_a_users = 0
    for video in pending_videos:
        if not video.user.id in  uploaded_users: # check if the user videos is already uploaded or not
            # Added this to background task to upload without waiting for the response
            background_tasks.add_task(await UploadVideo(video.user,video).upload())
            videos_of_a_users +=1
            # Upload 2 videos of each user daily to avoid cloudinary upload limit and also to avoid uploading all videos of a user if he has many pending videos
            if(videos_of_a_users >= 2):
                uploaded_users.append(video.user.id)
                videos_of_a_users = 0
                
    return {"message": "upload process triggered"}

 

# This is be trigger by schedule on DBOS
@router.post("/trigger-deleted-uploaded-files")
async def trigger_delete_uploaded_files(background_tasks: BackgroundTasks):
    background_tasks.add_task(await delete_uploaded_files())
    print("Delete uploaded files triggered")
    return {"message":"Delete uploaded files triggered"}


 

# ==========================================
# API KEY MANAGEMENT ENDPOINTS
# ==========================================


@router.post("/api/settings/generate-api-key")
async def generate_api_key(
    name: str = Query("Discovery Service"),
    current_user = Depends(get_current_user)
):
    """Generate an API key for external video discovery service"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Generate secure random token
        raw_token = secrets.token_urlsafe(32)
        
        # Hash it for storage (never store plain tokens)
        hashed = hashlib.sha256(raw_token.encode()).hexdigest()
        
        updated = await prisma.userapikey.create(
            data={
                "userId": current_user.id,
                "apiKey": hashed,
                "name": name
            }
        )

        # Return the plain token only once (user must save it)
        return {
            "success": True,
            "message": "API key generated. Save it securely!",
            "apiKey": raw_token,  # Only return once
            "userId": current_user.id,
            "expiresAt": updated.expiresAt
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/settings/api-key-status")
async def get_api_key_status(current_user = Depends(get_current_user)):
    """Check if user has an active API key"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        api_key = await prisma.userapikey.find_many(
            where={"userId": current_user.id}
        )

        result = []
        for key in api_key:
             result.append({
                "hasApiKey": True,
                "name": key.name,
                "isActive": key.isActive,
                "lastUsedAt": key.lastUsedAt,
                "createdAt": key.createdAt
            })
       
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/settings/api-key")
async def revoke_api_key(current_user = Depends(get_current_user)):
    """Revoke user's API key"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        await prisma.userapikey.update(
            where={"userId": current_user.id},
            data={"isActive": False}
        )

        return {"success": True, "message": "API key revoked"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# UPLOAD DEFAULTS ENDPOINT
# ==========================================

@router.get("/api/settings/upload-defaults")
async def get_upload_defaults(current_user = Depends(get_current_user)):
    """Get user's default upload destinations"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        user_config = await prisma.userconfig.find_unique(
            where={"userId": current_user.id}
        )

        if not user_config:
            return {
                "success": True,
                "destinations": ["tiktok"],  # Default
                "captionGenEnabled": True,
                "hashtagGenEnabled": True
            }

        destinations = user_config.defaultUploadDests if isinstance(user_config.defaultUploadDests, list) else ["tiktok"]
        captions = user_config.defaultCaption if isinstance(user_config.defaultCaption, str) else ""
        htags = user_config.defaultHtags if isinstance(user_config.defaultHtags, str) else ""

        return {
            "success": True,
            "destinations": destinations,
            "defaultCaption": captions,
            "defaultHtags": htags,
            "captionGenEnabled": True,
            "hashtagGenEnabled": True
        }

    except Exception as e:
        logger.error(f"Error fetching upload defaults: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/upload-defaults")
async def save_upload_defaults(
    upload_destinations: list = Query(...),
    current_user = Depends(get_current_user)
):
    """Save user's default upload destinations"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Receiving ["Youtube,Tiktok"] converting -> ["Youtube","Tiktok"]
        upload_destinations = [
            item.strip()
            for value in upload_destinations
            for item in value.split(',')
        ]
        print("Received upload destinations:", upload_destinations)

        # Validate destinations
        valid_destinations = ["Tiktok", "Youtube", "Facebook", "Instagram"]
        destinations = [d for d in upload_destinations if d in valid_destinations]
        print("after destinations:", destinations)

        if not destinations:
            raise HTTPException(status_code=400, detail="At least one valid destination required")

        # Get or create userconfig
        user_config = await prisma.userconfig.find_unique(
            where={"userId": current_user.id}
        )

        if user_config:
            # Update existing
            await prisma.userconfig.update(
                where={"userId": current_user.id},
                data={"defaultUploadDests": destinations}
            )
        else:
            # Create new
            await prisma.userconfig.create(
                data={
                    "userId": current_user.id,
                    "defaultUploadDests": destinations
                }
            )

        logger.info(f"✅ Upload defaults saved for user {current_user.id}: {destinations}")
        return {
            "success": True,
            "message": "Upload defaults saved",
            "destinations": destinations
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error saving upload defaults: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# VIDEO INGEST ENDPOINT (from discovery service)
# ==========================================

async def _process_discovered_videos(
    user_id: int,
    videos_data: list,
    upload_destinations: list,
    background_tasks: BackgroundTasks
):
    """Background task: Download and upload discovered videos using FromPlatform"""
    try:
        user = await prisma.user.find_unique(
            where={"id": user_id}, 
        )
        uploaded_successfully = 0
        uploaded_videos=[]
        for video_data in videos_data:
            try:
                video_url = video_data.get("videoUrl")
                video_id = video_data.get("externalId")
                platform = video_data.get("platform", "tiktok")

                is_duplicated = await prisma.video.find_first(
                    where={
                        "externalId": video_id,
                    }
                )

                if is_duplicated:
                    logger.warning("Duplicated video found, skipping: " + video_url)
                    continue

                if not video_url:
                    logger.warning("Skipping video without URL")
                    continue

                logger.info(f"🔄 Processing: {video_data.get('title')} from {platform}")

                # Use FromPlatform just like upload-from-url does
                down_and_upload_from = FromPlatform(
                    video_url=video_url,
                    user_id=user_id,
                    upload_targets=upload_destinations,
                    video_id=video_id   
                )
                 
                # Queue the actual download + upload to background
                background_tasks.add_task(await down_and_upload_from[platform])
                uploaded_successfully += 1

                uploaded_videos.append({
                        "url": video_url,
                        "title": video_data.get("title"),
                        "score":video_data.get("score"),
                        "platform": video_data.get("platform"),
                        "externalId": video_data.get("externalId")
                })

            except Exception as e:
                logger.error(f"Error processing video: {str(e)}")
                continue

            finally:
                if uploaded_successfully >= 2:  # Limit to 2 videos per run to prevent overload
                    print(f"Uploaded {uploaded_successfully} videos, stopping to prevent overload")
                    new_video_scheduled_mail(
                        recipient=user.email,
                         videos=uploaded_videos,user_name=user.fullname, default_upload_dest=upload_destinations
                        )
                    break
                else:
                    continue

        logger.info(f"✅ Queued {len(videos_data)} videos for user {user_id}")

    except Exception as e:
        logger.error(f"Fatal error in video processing: {str(e)}")


@router.post("/api/videos/ingest-discovered")
async def ingest_discovered_videos(
    payload: dict,
    authorization: str = Header(None),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Receive discovered videos from external discovery service
    Auth: Bearer <user-api-key>
    Downloads & uploads using FromPlatform (same as upload-from-url)
    """
    try:
        # 1. AUTHORIZATION CHECK
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid authorization")

        raw_token = authorization.replace("Bearer ", "").strip()
        hashed_token = hashlib.sha256(raw_token.encode()).hexdigest()

        api_key_record = await prisma.userapikey.find_unique(
            where={"apiKey": hashed_token}
        )

        if not api_key_record or not api_key_record.isActive:
            logger.warning(f"Invalid API key attempt")
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")

        user_id = api_key_record.userId

        # 2. GET USER DETAILS
        user = await prisma.user.find_unique(
            where={"id": user_id},
            include={ "userConfig": True}
        )

        if not user:
            logger.error(f"User {user_id} not found")
            raise HTTPException(status_code=404, detail="User not found")

        # 3. VALIDATE PAYLOAD
        videos = payload.get("videos", [])
        if not videos:
            return {"success": False, "message": "No videos provided"}

        if not isinstance(videos, list):
            raise HTTPException(status_code=400, detail="Videos must be an array")

        # Get upload destinations from user credentials
        upload_destinations = user.userConfig.defaultUploadDests
        print("Upload destinations from user config:", upload_destinations)
        logger.info(f"📥 Ingest: {len(videos)} videos for user {user_id}")

        # 4. QUEUE FOR PROCESSING
        background_tasks.add_task(
            _process_discovered_videos,
            user_id=user_id,
            videos_data=videos,
            upload_destinations=upload_destinations if isinstance(upload_destinations, list) else [upload_destinations] if upload_destinations else ["tiktok"],
            background_tasks=background_tasks
        )

        # Update API key last used
        await prisma.userapikey.update(
            where={"apiKey": hashed_token},
            data={"lastUsedAt": datetime.now()}
        )

        return {"success": True, "message": f"Processing {len(videos)} videos"}

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ingest error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))



# Update video details (title, captions, upload destinations)
@router.put("/api/videos/{video_id}")
async def update_video(
    video_id: int,
    request: UpdateVideoRequest,
    current_user = Depends(get_current_user)
):
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Check if video exists and belongs to the current user
        video = await prisma.video.find_unique(
            where={"id": video_id}
        )

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        if video.userId != current_user.id:
            raise HTTPException(status_code=403, detail="Unauthorized - Video does not belong to current user")

        # Prevent editing if video is already uploaded
        if video.upload_status == "Uploaded":
            raise HTTPException(status_code=409, detail="Cannot edit videos that have already been uploaded")

        # Prepare update data
        update_data = {}
        if request.title is not None:
            update_data["title"] = request.title
        if request.captions is not None:
            update_data["captions"] = request.captions
        if request.upload_destinations is not None and len(request.upload_destinations) > 0:
            update_data["upload_destinations"] = request.upload_destinations

        # Update the video
        updated_video = await prisma.video.update(
            where={"id": video_id},
            data=update_data
        )

        return {
            "message": "Video updated successfully",
            "video": {
                "id": updated_video.id,
                "title": updated_video.title,
                "captions": updated_video.captions,
                "upload_destinations": updated_video.upload_destinations
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error updating video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update video: {str(e)}")


@router.post("/api/videos/{video_id}/reschedule")
async def reschedule_video(
    video_id: int,
    current_user = Depends(get_current_user)
):
    """Reschedule a failed video by updating its status to Pending"""
    try:
        if isinstance(current_user, RedirectResponse):
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Check if video exists and belongs to the current user
        video = await prisma.video.find_unique(
            where={"id": video_id}
        )

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        if video.userId != current_user.id:
            raise HTTPException(status_code=403, detail="Unauthorized - Video does not belong to current user")

        # Check if video status is Failed
        if video.upload_status != "Failed":
            raise HTTPException(status_code=409, detail="Can only reschedule videos with Failed status")

        # Update status to Pending
        updated_video = await prisma.video.update(
            where={"id": video_id},
            data={"upload_status": "Pending"}
        )

        return {
            "message": "Video rescheduled successfully",
            "video": {
                "id": updated_video.id,
                "upload_status": updated_video.upload_status
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error rescheduling video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to reschedule video: {str(e)}")


# Connect Buffer - fetch profiles via GraphQL
@router.patch("/buffer-connect")
async def buffer_connect(body: BufferConnectRequest, current_user = Depends(get_current_user)):
    try:
        buffer_api_key = body.bufferApiKey.strip()
        
        if not buffer_api_key:
            raise HTTPException(status_code=400, detail="Buffer API key is required")
        
        # Save/update the API key in userconfig
        user_config = await prisma.userconfig.find_unique(
            where={"userId": current_user.id}
        )
        
        if user_config:
            # Update existing config
            await prisma.userconfig.update(
                where={"userId": current_user.id},
                data={"bufferApiKey": buffer_api_key}
            )
        else:
            # Create new config
            await prisma.userconfig.create(
                data={
                    "userId": current_user.id,
                    "bufferApiKey": buffer_api_key
                }
            )
        
        # Call the get_profiles_graphql function with the provided API key
        channels_array = get_profiles_graphql(api_key=buffer_api_key)
        channels = channels_array[::-1]
        user = await prisma.user.find_unique(where={"id": current_user.id})
        if channels is None:
            raise HTTPException(status_code=400, detail="Failed to fetch Buffer profiles. No channels found.")
        
        current_creds = dict(user.credentials) if user and user.credentials else {}

        # 2. Loop through channels and build the new dictionary in memory
        for channel in channels:
            provider = channel["service"].lower()
            channel_name = channel["name"]
            
            # Create a unique key so TikTok 1 and TikTok 2 don't collide
            unique_key = f"{provider}_{channel_name}" 
            
            token_data = {
                "client_id": channel["id"],
                "client_secret": channel["id"], 
                "name": channel_name, 
                "service": "buffer"
            }
            
            # Add or update it in our local dictionary
            # We serialize the inner token_data to a string based on your previous code structure
            current_creds[unique_key] = json.dumps(token_data)
            print(f"Prepared credentials for: {channel_name} under key '{unique_key}'")

        # 3. Convert the final, merged dictionary to a JSON string
        final_cre_json = json.dumps(current_creds)

        # 4. Do exactly ONE database update after the loop finishes
        await prisma.user.update(
            where={"id": user.id},
            data={
                "credentials": final_cre_json,
            }
        )

        return {"message": "Buffer connected successfully", "status": "connected", "channels": channels}
    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=400, detail=f"Failed to connect to Buffer: {str(e)}")


# Dynamic edits for anti-copyright transformations
@router.get("/api/settings/dynamic-edits")
async def get_dynamic_edits(current_user: dict = Depends(get_current_user)):
    """Fetch user's dynamic edit configuration"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    dynamic_edits = await prisma.dynamicedit.find_unique(
        where={"userId": current_user.id}
    )

    
    if not dynamic_edits:
        return {
            "audioControls": {},
            "textOverlayControls": {},
            "pixelDistortionControls": {}
        }
    
    return {
        "audioControls": dynamic_edits.audioControls,
        "textOverlayControls": dynamic_edits.textOverlayControls,
        "pixelDistortionControls": dynamic_edits.pixelDistortionControls
    }


# ==========================================
# VIDEO PLAYGROUND API ENDPOINTS
# ==========================================

@router.post("/api/playground/build-transformation")
async def build_transformation(
    request: PlaygroundTransformationRequest,
    current_user: dict = Depends(get_current_user)
):
    """Build Cloudinary transformation URL from playground settings"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    # This endpoint is kept for backwards compatibility
    # The playground is now UI-only and saves config to dynamicEdits
    # Actual Cloudinary transformations happen during video upload
    try:
        transformer = CloudinaryVideoTransformer(request.video_url)
        
        # Add Video Layers if specified
        if request.overlay_type != "none":
            transformer.add_video_layers(
                overlay_type=request.overlay_type,
                overlay_text=request.overlay_text,
                font_size=request.font_size,
                text_color=request.text_color,
                text_opacity=request.text_opacity
            )
        
        # Add Resizing & Cropping
        if request.crop_mode != "none":
            transformer.add_resizing_cropping(
                crop_mode=request.crop_mode,
                width=request.width,
                height=request.height,
                aspect_ratio=request.aspect_ratio,
                gravity=request.gravity,
                x_offset=request.x_offset,
                y_offset=request.y_offset
            )
        
        # Add Auto Cropping
        if request.auto_crop_type != "none":
            transformer.add_auto_cropping(
                crop_type=request.auto_crop_type,
                width=request.width,
                height=request.height
            )
        
        # Add Trimming
        if request.start_offset or request.end_offset or request.duration:
            transformer.add_trimming(
                start_offset=request.start_offset,
                end_offset=request.end_offset,
                duration=request.duration
            )
        
        # Add Quality & Format
        transformer.add_quality_format(
            quality=request.quality,
            dpr=request.dpr
        )
        
        # Build URL
        transformation_url = transformer.build_url()
        
        return {
            "success": True,
            "url": transformation_url,
            "transformations": transformer.get_transformation_chain()
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Transformation error: {str(e)}")


@router.get("/api/playground/presets")
async def get_transformation_presets(current_user: dict = Depends(get_current_user)):
    """Get pre-built transformation presets for common use cases"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    return {
        "presets": {
            "instagram_reel": {
                "name": "Instagram Reel",
                "description": "Optimized for 9:16 vertical video",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "9:16",
                    "width": 1080,
                    "height": 1920,
                    "quality": "auto:good"
                }
            },
            "youtube_thumbnail": {
                "name": "YouTube Thumbnail",
                "description": "16:9 widescreen format",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "16:9",
                    "width": 1280,
                    "height": 720,
                    "quality": "90"
                }
            },
            "tiktok_vertical": {
                "name": "TikTok Vertical",
                "description": "Optimized vertical format for TikTok",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "9:16",
                    "width": 1080,
                    "height": 1920,
                    "auto_crop_type": "face"
                }
            },
            "square_short": {
                "name": "Square Short",
                "description": "1:1 square format for social media",
                "config": {
                    "crop_mode": "fill",
                    "aspect_ratio": "1:1",
                    "width": 1080,
                    "height": 1080,
                    "gravity": "auto"
                }
            },
            "mobile_optimized": {
                "name": "Mobile Optimized",
                "description": "Optimized for mobile viewing",
                "config": {
                    "crop_mode": "scale",
                    "width": 720,
                    "height": 1280,
                    "dpr": "auto",
                    "quality": "auto"
                }
            },
            "high_quality_desktop": {
                "name": "High Quality Desktop",
                "description": "Premium quality for desktop",
                "config": {
                    "crop_mode": "fill",
                    "width": 1920,
                    "height": 1080,
                    "quality": "95",
                    "dpr": "1"
                }
            },
            "with_watermark": {
                "name": "With Watermark",
                "description": "Add brand watermark overlay",
                "config": {
                    "overlay_type": "text",
                    "overlay_text": "@yourbrand",
                    "text_color": "#F97316",
                    "text_opacity": 80,
                    "font_size": 48,
                    "gravity": "south_east"
                }
            },
            "auto_crop_faces": {
                "name": "Auto Crop Faces",
                "description": "AI-powered automatic face detection",
                "config": {
                    "crop_mode": "fill",
                    "auto_crop_type": "face",
                    "width": 1280,
                    "height": 720,
                    "gravity": "auto"
                }
            }
        }
    }


@router.post("/api/settings/dynamic-edits")
async def save_dynamic_edits(request: DynamicEditRequest, current_user: dict = Depends(get_current_user)):
    """Save user's dynamic edit configuration"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        ) 
        
        if existing:
            # Update existing record
            dynamic_edits = await prisma.dynamicedit.update(
                where={"userId": current_user.id},
                data={
                    "audioControls": json.dumps(request.audioControls),
                    "textOverlayControls": json.dumps(request.textOverlayControls),
                    "pixelDistortionControls": json.dumps(request.pixelDistortionControls),
                }
            )
        else:
            # Create new record
            dynamic_edits = await prisma.dynamicedit.create(
                data={
                    "userId": current_user.id,
                    "audioControls": json.dumps(request.audioControls),
                    "textOverlayControls": json.dumps(request.textOverlayControls),
                    "pixelDistortionControls": json.dumps(request.pixelDistortionControls),
                }
            )
        
        return {
            "success": True,
            "message": "Dynamic edits saved successfully",
            "data": {
                "audioControls": json.loads(dynamic_edits.audioControls) if isinstance(dynamic_edits.audioControls, str) else dynamic_edits.audioControls,
                "textOverlayControls": json.loads(dynamic_edits.textOverlayControls) if isinstance(dynamic_edits.textOverlayControls, str) else dynamic_edits.textOverlayControls,
                "pixelDistortionControls": json.loads(dynamic_edits.pixelDistortionControls) if isinstance(dynamic_edits.pixelDistortionControls, str) else dynamic_edits.pixelDistortionControls,
            }
        }
    except Exception as e:
        print(f"Error saving dynamic edits: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save dynamic edits: {str(e)}")


@router.get("/api/playground/init")
async def initialize_playground(current_user: dict = Depends(get_current_user)):
    """Initialize playground - check if user has dynamicEdits and load saved settings"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        if existing:
            # Parse saved playground transformations
            try:
                playground_data = json.loads(existing.playgroundTransformations) if isinstance(existing.playgroundTransformations, str) else existing.playgroundTransformations
            except:
                playground_data = None
            
            return {
                "success": True,
                "hasDynamicEdits": True,
                "savedTransformations": playground_data
            }
        else:
            return {
                "success": True,
                "hasDynamicEdits": False,
                "savedTransformations": None
            }
    except Exception as e:
        print(f"Error initializing playground: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize playground: {str(e)}")


@router.post("/api/playground/activate")
async def activate_playground(request: dict, current_user: dict = Depends(get_current_user)):
    """Activate anti-copyright and create dynamicEdits if it doesn't exist"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        default_data = {
            "audioControls": {
                "enabled": True,
                "codec": "aac",
                "frequency": 44100
            },
            "textOverlayControls": {
                "enabled": True
            },
            "pixelDistortionControls": {
                "enabled": True,
                "noise": 0,
                "vignette": 0
            }
        }
        
        # Default playground config
        default_playground = {
            "video_url": "https://cdn.pixabay.com/video/2022/02/06/106883-674268697_large.mp4",
            "overlay_type": "none",
            "overlay_text": "@brand",
            "font_size": 36,
            "text_color": "#F97316",
            "text_opacity": 100,
            "crop_mode": "none",
            "width": None,
            "height": None,
            "aspect_ratio": "",
            "gravity": "",
            "x_offset": 0,
            "y_offset": 0,
            "start_offset": None,
            "end_offset": None,
            "duration": None,
            "quality": "",
            "dpr": "",
            "auto_crop_type": "none"
        }
        
        if existing:
            # Update with anti-copyright settings
            await prisma.dynamicedit.update(
                where={"userId": current_user.id},
                data={
                    "audioControls": json.dumps(default_data["audioControls"]),
                    "textOverlayControls": json.dumps(default_data["textOverlayControls"]),
                    "pixelDistortionControls": json.dumps(default_data["pixelDistortionControls"]),
                    "playgroundTransformations": json.dumps(default_playground)
                }
            )
        else:
            # Create new record with defaults
            await prisma.dynamicedit.create(
                data={
                    "userId": current_user.id,
                    "audioControls": json.dumps(default_data["audioControls"]),
                    "textOverlayControls": json.dumps(default_data["textOverlayControls"]),
                    "pixelDistortionControls": json.dumps(default_data["pixelDistortionControls"]),
                    "playgroundTransformations": json.dumps(default_playground)
                }
            )
        
        return {
            "success": True,
            "message": "Anti-Copyright settings activated",
            "defaultSettings": default_data
        }
    except Exception as e:
        print(f"Error activating anti-copyright: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to activate: {str(e)}")


@router.post("/api/playground/save")
async def save_playground_transformations(request: PlaygroundConfigRequest, current_user: dict = Depends(get_current_user)):
    """Save playground transformation configuration to dynamicEdits (UI preview only)"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        # Check if record exists
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        if not existing:
            # Auto-create if doesn't exist
            await prisma.dynamicedit.create(
                data={
                    "userId": current_user.id,
                    "audioControls": "{}",
                    "textOverlayControls": "{}",
                    "pixelDistortionControls": "{}",
                    "playgroundTransformations": json.dumps(request.dict())
                }
            )
        else:
            # Update existing record with playground config
            await prisma.dynamicedit.update(
                where={"userId": current_user.id},
                data={
                    "playgroundTransformations": json.dumps(request.dict()),
                }
            )
        
        return {
            "success": True,
            "message": "Transformation settings saved successfully",
            "transformations": request.dict()
        }
    except Exception as e:
        print(f"Error saving playground transformations: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}")


@router.get("/api/playground/load")
async def load_playground_transformations(current_user: dict = Depends(get_current_user)):
    """Load playground transformation configuration from dynamicEdits"""
    if isinstance(current_user, RedirectResponse):
        return current_user
    
    try:
        existing = await prisma.dynamicedit.find_unique(
            where={"userId": current_user.id}
        )
        
        if existing and existing.textOverlayControls:
            try:
                text_controls = json.loads(existing.textOverlayControls) if isinstance(existing.textOverlayControls, str) else existing.textOverlayControls
                playground_data = text_controls.get("playground")
                
                if playground_data:
                    return {
                        "success": True,
                        "transformations": playground_data
                    }
            except:
                pass
        
        return {
            "success": False,
            "message": "No saved playground settings found"
        }
    except Exception as e:
        print(f"Error loading playground transformations: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load: {str(e)}")