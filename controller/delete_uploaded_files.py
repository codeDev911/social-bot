from db import prisma
import asyncio
import os
import cloudinary
from controller.mail_controller import trigger_deleted
from datetime import datetime


async def delete_uploaded_files():

    try:
        # Delete 20 videos from cloudinary to maintain storage 
        uploaded_videos = await prisma.video.find_many(
            where={
                "upload_status":"Uploaded"
            },
            take=20,
            include={"user": True},
        )


        if len(uploaded_videos) == 0:
            print("No video found to delete")
            return {"message":"No uploaded video founded to delete"}

        delete_vd_ids = [v.cloudinary_id for v in uploaded_videos if v.cloudinary_id]
        if not delete_vd_ids:
            return {"Error":"No Cloudinary ids found on the uploaded videos"}

        # The Cloudinary admin API is blocking; keep it off the event loop.
        # delete_resources expects public ids, which is what we now store.
        result = await asyncio.to_thread(
            cloudinary.api.delete_resources,
            delete_vd_ids,
            resource_type="video",
        )

        deleted = result.get("deleted") or {}
        succeeded = [public_id for public_id, status in deleted.items() if status == "deleted"]

        if not succeeded:
            print(f"Cloudinary deleted nothing: {result}")
            return {"Error":"Error occured while delete video from cloudinary", "details": result}

        for vi in uploaded_videos:
            if vi.cloudinary_id not in succeeded:
                continue
            await prisma.video.update(
                where={
                    "id": vi.id
                },
                data={
                    "upload_status":"Deleted"
                }
            )
            print("Files deleted successfully")
            # Send email to user when files deleted
            await asyncio.to_thread(
                trigger_deleted,
                vi.user.email,
                os.path.basename((vi.cloudinary_url or "").split("?")[0]),
                vi.title,
                "N/A",
                datetime.utcnow().strftime("%d-%m-%Y %I-%M %p"),
                "Deleted",
            )

        return {"message": "Files deleted successfully", "details": deleted}

    except Exception as e:
        print(f"Error occured : {e}")
        return {"Error":f"Error occured : {e}"}
