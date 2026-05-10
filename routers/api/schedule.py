from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime
import os
from controller.scheduler import create_user_job, update_user_job, delete_user_job
from lib.depends import get_current_user
from db import prisma as db

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

# Request Models
class ScheduleCreate(BaseModel):
    hour: int  # 0-23
    minute: int  # 0-59
    timezone: str = "UTC"

class ScheduleUpdate(BaseModel):
    hour: int
    minute: int
    timezone: str = "UTC"

class ScheduleResponse(BaseModel):
    scheduled: str
    scheduledId: int | None
    hour: int
    minute: int
    timezone: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# GET - Retrieve user's schedule
@router.get("/", response_model=ScheduleResponse)
async def get_schedule(user = Depends(get_current_user)):
    """Get the user's current schedule configuration."""
    try:
        user_config = await db.userconfig.find_unique(where={"userId": user.id})
        

        if not user_config.scheduledId:
            raise HTTPException(status_code=404, detail="User config not found")
        

        parts = user_config.scheduled.split("-") if user_config.scheduled else []

        return ScheduleResponse(
            scheduled=user_config.scheduled,
            scheduledId=user_config.scheduledId,
           hour = int(parts[0]) if len(parts) > 0 else 0,
            minute = int(parts[1]) if len(parts) > 1 else 0,
            timezone=user_config.timezone,
            created_at=user_config.createdAt,
            updated_at=user_config.updatedAt
        )
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"Error fetching schedule: {str(e)}")

# POST - Create a new schedule
@router.post("/", response_model=dict)
async def create_schedule(schedule: ScheduleCreate, user = Depends(get_current_user)):
    """Create a new upload schedule for the user."""
    try:
        # Get user config
        user_config = await db.userconfig.find_unique(where={"userId": user.id})
        
        if not user_config:
            raise HTTPException(status_code=404, detail="User config not found")
        
        # Check if schedule already exists
        if user_config.scheduled and user_config.scheduledId:
            raise HTTPException(
                status_code=400, 
                detail="User already has a schedule. Use PUT to update it."
            )
        
        # Validate time
        if not (0 <= schedule.hour <= 23):
            raise HTTPException(status_code=400, detail="Hour must be between 0-23")
        if not (0 <= schedule.minute <= 59):
            raise HTTPException(status_code=400, detail="Minute must be between 0-59")
        
        # Create cron job
        job_response = create_user_job(
            user_id=user.id,
            hour=schedule.hour,
            minute=schedule.minute,
            timezone=schedule.timezone
        )
        
        job_id = job_response.get("jobId")
        if not job_id:
            raise HTTPException(status_code=500, detail="Failed to create cron job")
        
        # Update user config with schedule
        updated_config = await db.userconfig.update(
            where={"userId": user.id},
            data={
                "scheduled":f"{schedule.hour}-{schedule.minute}" ,
                "scheduledId": job_id,
                "timezone": schedule.timezone
            }
        )
        
        return {
            "success": True,
            "message": "Schedule created successfully",
            "scheduledId": job_id,
            "hour": schedule.hour,
            "minute": schedule.minute,
            "timezone": schedule.timezone
        }
    
    except HTTPException:
        print("HTTPException occurred while creating schedule")
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"Error creating schedule: {str(e)}")

# PUT - Update existing schedule
@router.put("/", response_model=dict)
async def update_schedule(schedule: ScheduleUpdate, user = Depends(get_current_user)):
    """Update an existing upload schedule."""
    try:
        # Get user config
        user_config = await db.userconfig.find_unique(where={"userId": user.id})
        
        if not user_config:
            raise HTTPException(status_code=404, detail="User config not found")
        
        # Check if schedule exists
        if  not user_config.scheduledId:
            raise HTTPException(
                status_code=404,
                detail="No schedule found. Use POST to create one."
            )
        
        # Validate time
        if not (0 <= schedule.hour <= 23):
            raise HTTPException(status_code=400, detail="Hour must be between 0-23")
        if not (0 <= schedule.minute <= 59):
            raise HTTPException(status_code=400, detail="Minute must be between 0-59")
        
        # Update cron job
        job_response = update_user_job(
            job_id=user_config.scheduledId,
            new_hour=schedule.hour,
            new_minute=schedule.minute
        )
        
        # Update user config
        updated_config = await db.userconfig.update(
            where={"userId": user.id},
            data={
                "scheduled":f"{schedule.hour}-{schedule.minute}" ,
                "timezone": schedule.timezone
            }
        )
        
        return {
            "success": True,
            "message": "Schedule updated successfully",
            "scheduledId": user_config.scheduledId,
            "hour": schedule.hour,
            "minute": schedule.minute,
            "timezone": schedule.timezone
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating schedule: {str(e)}")

# DELETE - Remove schedule
@router.delete("/", response_model=dict)
async def delete_schedule(user = Depends(get_current_user)):
    """Delete the user's upload schedule."""
    try:
        # Get user config
        user_config = await db.userconfig.find_unique(where={"userId": user.id})
        
        if not user_config:
            raise HTTPException(status_code=404, detail="User config not found")
        
        # Check if schedule exists
        if not user_config.scheduled or not user_config.scheduledId:
            raise HTTPException(status_code=404, detail="No schedule to delete")
        
        # Delete cron job
        success = delete_user_job(user_config.scheduledId)
        
        if not success:
            print("Failed to delete cron job with ID:", user_config.scheduledId)
            raise HTTPException(status_code=500, detail="Failed to delete cron job")
        
        # Update user config
        await db.userconfig.update(
            where={"userId": user.id},
            data={
                "scheduled": False,
                "scheduledId": None
            }
        )
        
        return {
            "success": True,
            "message": "Schedule deleted successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"Error deleting schedule: {str(e)}")
