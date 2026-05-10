from fastapi import  Depends, HTTPException, Request
from dotenv import load_dotenv
import os

from fastapi.responses import RedirectResponse
from db import prisma
import jwt

load_dotenv()
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"

async def get_jwt_token(request:Request):
    """
    Retrieve the JWT token from the Authorization header or cookies.
    """
    authorization = request.headers.get("authorization")
    jwt_token = request.cookies.get("token")
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ")[1]
    elif jwt_token:
        return jwt_token
    else:
        return None


async def get_current_user(request:Request,token: str = Depends(get_jwt_token)):
    if not token:
        return RedirectResponse(url="/login")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user = await prisma.user.find_first(where={"email":payload["sub"]})

        if not user:
            return RedirectResponse(url="/login")
        return user
    
    except jwt.PyJWTError:
        return RedirectResponse(url="/login")
     
     