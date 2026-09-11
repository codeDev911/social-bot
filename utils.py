import os
import re
from fastapi import HTTPException, Query
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv
import jwt
from datetime import datetime, timedelta
import secrets

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
SCOPES = SCOPES = "https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/youtube"


import base64
import hashlib
import secrets

def generate_code_verifier() -> str:
    """Generates a cryptographically random PKCE code verifier."""
    # Generate 32 cryptographically random bytes
    random_bytes = secrets.token_bytes(32)
    # URL-safe base64 encode and strip the padding ('=')
    return base64.urlsafe_b64encode(random_bytes).rstrip(b'=').decode('utf-8')

def generate_code_challenge(verifier: str) -> str:
    """Generates a SHA-256 PKCE code challenge from a verifier."""
    # Create a SHA-256 hash of the verifier string
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    # URL-safe base64 encode the hash and strip the padding ('=')
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('utf-8')



def get_google_auth_url(state:any = None):
    """Generate the Google OAuth URL."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,  # Optional: You can use this to maintain state between request and callback
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def get_tiktok_auth_url(state:any = None):
    """Generate the TikTok OAuth URL."""
    params = {
        "client_key": os.getenv("TIKTOK_CLIENT_ID"),
        "redirect_uri": os.getenv("TIKTOK_REDIRECT_URI"),
        "response_type": "code",
        "scope": "user.info.basic,user.info.profile,video.publish,video.upload",
        "state": state,  
    }
    return f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"

def get_buffer_auth_url(state:any = None):
    """Generate the Buffer OAuth URL."""
    params = {
        "client_id": os.getenv("BUFFER_CLIENT_ID"),
        "redirect_uri": os.getenv("BUFFER_REDIRECT_URI"),
        "response_type": "code",
        "scope": "posts:write posts:read ideas:read ideas:write account:read account:write offline_access",
        "state": state,  
        "code_challenge":generate_code_challenge(generate_code_verifier()),
        "code_challenge_method":"S256",
        "prompt":"consent",
    }
    return f"https://auth.buffer.com/auth/?{urlencode(params)}"

def exchange_code_for_tokens(code: str):
    """Exchange the authorization code for tokens."""
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    response = requests.post(token_url, data=data)
    response.raise_for_status()  # Raise an error for bad responses
    return response.json()

 
def refresh_tiktok_access_token(refresh_token: str):
    url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {
        "Authorization": f"Bearer {refresh_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data={
        "client_key": os.getenv("TIKTOK_CLIENT_ID"),
        "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    response = requests.post(url, headers=headers,data=data)
    response.raise_for_status()
    access_token = response.json()["access_token"]
    return access_token

# Jwt 
SECRET_KEY = os.getenv("JWT_SECRET_KEY")

# Create JWT Token
def create_access_token(data: dict, expires_delta: timedelta = timedelta(minutes=60*24*7)):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
    return encoded_jwt

# Verify Upload Target
def verify_upload_target(upload_targets: list = Query(..., description="Verify upload targets")) -> list:
    """Verify if the upload target is valid."""
    valid_targets = ['Youtube', 'Facebook', 'Instagram', 'Tiktok']
    upload_targets = upload_targets[0].split(",")
    
    for target in upload_targets:
        if target not in valid_targets:
            raise HTTPException(status_code=400, detail="Invalid upload target. Supported targets are: Youtube, Facebook, Instagram, Tiktok")
    return upload_targets



# Validate video URL
TIKTOK_URL_PATTERN = r"^(https?://)?(www\.)?tiktok\.com/.*"
INSTAGRAM_URL_PATTERN = r"^(https?://)?(www\.)?instagram\.com/.*"
TIKTOK_2ND_URL_PATTERN = r"^(https?://)?(vt\.)?tiktok\.com/.*"
PEXELS_URL_PATTERN = r"^(https?://)?(videos\.)?pexels\.com/.*"
FACEBOOK_URL_PATTERN = r"^(https?://)?(www\.)?facebook\.com/.*"
FACEBOOK_2ND_URL_PATTERN = r"^(https?://)?(m\.)?facebook\.com/.*"
YOUTUBE_URL_PATTERN = r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.*"
def validate_video_url(url: str = Query(..., description="Video URL to verify")) -> str:
    if re.match(INSTAGRAM_URL_PATTERN, url):
        return "instagram"
    if re.match(TIKTOK_URL_PATTERN, url):
        return "tiktok"
    elif re.match(TIKTOK_2ND_URL_PATTERN, url):
        return "tiktok"
    elif re.match(FACEBOOK_URL_PATTERN, url):
        return "facebook"
    elif re.match(FACEBOOK_2ND_URL_PATTERN, url):
        return "facebook"
    elif re.match(YOUTUBE_URL_PATTERN, url):
        return "youtube"
    elif re.match(PEXELS_URL_PATTERN, url):
        return "pexels"
    else:
        raise HTTPException(status_code=400, detail="Invalid video URL, We are currently support urls of tiktok, facebook and youtube only")