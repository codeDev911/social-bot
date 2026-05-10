from db import prisma
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
        )


        if len(uploaded_videos) == 0:
            print("No video found to delete")
            return {"message":"No uploaded video founded to delete"}

        delete_vd_ids = []

        for video in uploaded_videos:
            delete_vd_ids.append(video.cloudinary_id)
        
        result  = cloudinary.api.delete_resources(delete_vd_ids)
        
        if result.get("deleted"):
            for v_id in delete_vd_ids:
                vi = await prisma.video.update(
                    where={
                        "cloudinary_id":v_id
                    },
                    data={
                        "upload_status":"Deleted"
                    }
                )
                print("Files deleted successfully")
                # Send email to user when files deleted
                trigger_deleted(vi.user.email,vi.file_name,vi.title,"N/A",datetime.utcnow().strftime("%d-%m-%Y %I-%M %p"),"Deleted")
                return {"message": "Files deleted successfully", "details": result["deleted"]}
        else:
            print("Error occured while delete video from cloudinary")
            return {"Error":"Error occured while delete video from cloudinary"}
            
    except Exception as e:
        print(f"Error occured : {e}")
        return {"Error":f"Error occured : {e}"}
