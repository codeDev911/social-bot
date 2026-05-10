# feature_checker.py
from fastapi import HTTPException
from .plans import PLANS

class FeatureChecker:
    def __init__(self, user_plan: str):
        if user_plan not in PLANS:
            raise HTTPException(status_code=403, detail="Invalid subscription plan")
        self.plan = PLANS[user_plan]

    def can_upload(self, uploads_today: int):
        if uploads_today >= self.plan["max_upload_per_day"]:
            raise HTTPException(status_code=403, detail="Upload limit reached")
        return True
    
    def can_schedule(self, scheduled_today: int):
        if scheduled_today >= self.plan["max_scheduled_videos"]:
            raise HTTPException(status_code=403, detail="Scheduled limit reached")
        return True
    

    def can_connect_account(self, connected: int):
        if connected >= self.plan["max_account_connection"]:
            raise HTTPException(status_code=403, detail="Account connection limit reached")
        return True


    def can_access_feature(self, feature: str):
        if feature not in self.plan:
            raise HTTPException(status_code=404, detail=f"Feature {feature} not found")
        if not self.plan[feature]:
            raise HTTPException(status_code=403, detail=f"Feature {feature} not allowed")
        return True
    
    def can_send_mail_on_uploads(self) -> bool:
        if self.plan["send_mail_on_upload"]:
            return True
        else:
            return False

    def run_service(self, feature: str):
        if feature not in self.plan:
            raise HTTPException(status_code=404, detail=f"Service {feature} not found")
        func = self.plan[feature]
        if callable(func):
            return func()
        return self.plan[feature]
