from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from db import prisma as db
from lib.depends import get_current_user

router = APIRouter(prefix="/api/user", tags=["user"])

# Request Models
class UserConfigUpdate(BaseModel):
    defaultCaption: str | None = None
    defaultHtags: str | None = None
    defaultUploadDest: str | None = None
    captionGenEnabled: bool | None = None
    hashtagGenEnabled: bool | None = None
    emailOnUpload: bool | None = None
    timezone: str | None = None
    bufferApiKey: str | None = None

class UserProfileUpdate(BaseModel):
    fullname: str | None = None
    email: str | None = None

class UserConfigResponse(BaseModel):
    id: int
    defaultCaption: str
    defaultHtags: str
    defaultUploadDest: str
    captionGenEnabled: bool
    hashtagGenEnabled: bool
    emailOnUpload: bool
    timezone: str
    bufferApiKey: str | None = None

    class Config:
        from_attributes = True

# GET - Retrieve user config
@router.get("/config", )
async def get_config(user = Depends(get_current_user)):
    """Get the user's configuration settings."""
    try:
        user_config = await db.userconfig.find_unique(where={"userId": user.id},)
        
        if not user_config:
            # Create default config if doesn't exist
            user_config = await db.userconfig.create(
                data={"userId": user.id},
            )
        return {
            "id":user_config.id,
            "defaultCaption":user_config.defaultCaption,
            "defaultHtags":user_config.defaultHtags,
            "defaultUploadDest":user_config.defaultUploadDest,
            "captionGenEnabled":user_config.captionGenEnabled if hasattr(user_config, 'captionGenEnabled') else True,
            "hashtagGenEnabled":user_config.hashtagGenEnabled if hasattr(user_config, 'hashtagGenEnabled') else True,
            "emailOnUpload":user_config.emailOnUpload,
            "timezone":user_config.timezone,
            "bufferApiKey":user_config.bufferApiKey if hasattr(user_config, 'bufferApiKey') else None
        }
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"Error fetching config: {str(e)}")

# PATCH - Update user config
@router.patch("/config", response_model=dict)
async def update_config(config: UserConfigUpdate, user = Depends(get_current_user)):
    """Update user configuration settings."""
    try:
        # Get or create config
        user_config = await db.userconfig.find_unique(where={"userId": user.id})
        
        if not user_config:
            user_config = await db.userconfig.create(
                data={"userId": user.id}
            )
        
        # Prepare update data (only include fields that are not None)
        update_data = {}
        if config.defaultCaption is not None:
            update_data["defaultCaption"] = config.defaultCaption
        if config.defaultHtags is not None:
            update_data["defaultHtags"] = config.defaultHtags
        if config.defaultUploadDest is not None:
            update_data["defaultUploadDest"] = config.defaultUploadDest
        if config.captionGenEnabled is not None:
            update_data["captionGenEnabled"] = config.captionGenEnabled
        if config.hashtagGenEnabled is not None:
            update_data["hashtagGenEnabled"] = config.hashtagGenEnabled
        if config.emailOnUpload is not None:
            update_data["emailOnUpload"] = config.emailOnUpload
        if config.timezone is not None:
            update_data["timezone"] = config.timezone
        if config.bufferApiKey is not None:
            update_data["bufferApiKey"] = config.bufferApiKey
        
        # Update config
        updated_config = await db.userconfig.update(
            where={"userId": user.id},
            data=update_data
        )
        
        return {
            "success": True,
            "message": "Config updated successfully",
            "config": {
                "defaultCaption": updated_config.defaultCaption,
                "defaultHtags": updated_config.defaultHtags,
                "defaultUploadDest": updated_config.defaultUploadDest,
                "captionGenEnabled": updated_config.captionGenEnabled if hasattr(updated_config, 'captionGenEnabled') else True,
                "hashtagGenEnabled": updated_config.hashtagGenEnabled if hasattr(updated_config, 'hashtagGenEnabled') else True,
                "emailOnUpload": updated_config.emailOnUpload,
                "timezone": updated_config.timezone,
                "bufferApiKey": updated_config.bufferApiKey if hasattr(updated_config, 'bufferApiKey') else None
            }
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating config: {str(e)}")

# PATCH - Update user profile
@router.patch("/profile", response_model=dict)
async def update_profile(profile: UserProfileUpdate, user = Depends(get_current_user)):
    """Update user profile information."""
    try:
        update_data = {}
        
        if profile.fullname is not None:
            update_data["fullname"] = profile.fullname
        if profile.email is not None:
            # Check if email already exists
            existing_user = await db.user.find_unique(where={"email": profile.email})
            if existing_user and existing_user.id != user.id:
                raise HTTPException(status_code=400, detail="Email already in use")
            update_data["email"] = profile.email
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        updated_user = await db.user.update(
            where={"id": user.id},
            data=update_data
        )
        
        return {
            "success": True,
            "message": "Profile updated successfully",
            "user": {
                "fullname": updated_user.fullname,
                "email": updated_user.email
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating profile: {str(e)}")
