import json
import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# Googe YT client
async def get_youtube_client(credentials): 
    """Get Youtube client Credentials.
        To access Client youtube account  """ 
    cre_obj = credentials["google"] # Credentials object 
    refresh_token = ""
    try:
        refresh_token = cre_obj["refresh_token"] 
        
    except Exception as e:
        refresh_token = json.loads(cre_obj)["refresh_token"] 
    
    # New
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=credentials)


def get_tiktok_client(credentials):
    """Get Tiktok Client Credentials"""
    cre_obj = json.loads(credentials["tiktok"])
    
    return cre_obj

async def get_facebook_client(credentials):
    """Get Facebook Client Credentials"""
    cre_obj = credentials["facebook"]

    return cre_obj

async def get_instagram_client(credentials):
    """Get Instagram Client Credentials"""
    cre_obj = credentials["instagram"]

    return cre_obj