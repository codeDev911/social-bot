import requests
from dotenv import load_dotenv
import os
load_dotenv()

CRON_API_KEY = os.getenv("CRON_JOB_API_KEY") # Get from Settings > API Keys
BASE_URL = "https://api.cron-job.org"
HEADERS = {"Authorization": f"Bearer {CRON_API_KEY}", "Content-Type": "application/json"}
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET") # A secret key to verify incoming webhook requests

# 1. CREATE A JOB
def create_user_job(user_id: str, hour: int, minute: int, timezone: str = "Asia/Karachi"):
    webhook_url = f"{os.getenv("APP_API_URL")}dashboard/trigger-upload?user_id={user_id}&secret={INTERNAL_SECRET}"
    
    payload = {
        "job": {
            "title": f"Upload-User-{user_id}",
            "url": webhook_url,
            "enabled": True,
            "saveResponses": True,
            "schedule": {
                "timezone": timezone,
                "hours": [hour],    # e.g., 20 for 8 PM
                "minutes": [minute], 
                "mdays": [-1], "months": [-1], "wdays": [-1] # Run every day
            }
        }
    }
    # Note: cron-job.org uses PUT for creating NEW jobs
    response = requests.put(f"{BASE_URL}/jobs", json=payload, headers=HEADERS)
    return response.json() # Use the returned 'jobId' to update/delete later

# 2. UPDATE A JOB
def update_user_job(job_id: int, new_hour: int, new_minute: int):
    # Updating requires the specific jobId you got during creation
    payload = {
        "job": {
            "schedule": {
                "hours": [new_hour],
                "minutes": [new_minute]
            }
        }
    }
    response = requests.patch(f"{BASE_URL}/jobs/{job_id}", json=payload, headers=HEADERS)
    return response.json()

# 3. DELETE A JOB
def delete_user_job(job_id: int):
    response = requests.delete(f"{BASE_URL}/jobs/{job_id}", headers=HEADERS)
    return response.status_code == 200 or response.status_code == 201
