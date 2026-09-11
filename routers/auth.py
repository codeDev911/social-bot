from fastapi import APIRouter, Request,Depends
from fastapi.responses import RedirectResponse, Response
from utils import  get_google_auth_url, get_tiktok_auth_url,get_buffer_auth_url  
from controller.auth import authGoogle, authTiktok,authBuffer
from lib.depends import get_current_user
router = APIRouter(prefix="/auth", tags=["auth"])


# Auth
@router.get("/login/google")
async def google_login():
    """Redirect to Google's OAuth login."""
    auth_url = get_google_auth_url()
    return RedirectResponse(auth_url)


@router.get("/google/callback")
async def google_callback(code: str, state: str = None):

    # passing state to identify the user who wants to connect his google account
    return await authGoogle(code,state)


@router.get("/login/tiktok")
async def google_login():
    """Redirect to Tiktok's OAuth login."""
    auth_url = get_tiktok_auth_url()
    return RedirectResponse(auth_url) 

@router.get("/tiktok/callback")
async def tiktok_callback(code: str,state: str = None):
    # passing state to identify the user who wants to connect his tiktok account

    return await authTiktok(code,state)


@router.get("/login/buffer")
async def google_login():
    """Redirect to buffer's OAuth login."""
    auth_url = get_buffer_auth_url()
    return RedirectResponse(auth_url)


@router.get("/buffer/callback")
async def google_callback(code: str, state: str = None):

    # passing state to identify the user who wants to connect his google account
    return await authBuffer(code,state)



# Connect accounts
@router.get("/connect/youtube")
async def tiktok_connect(current_user: dict = Depends(get_current_user)):
    """Redirect to Tiktok's OAuth login."""
    # Pass user id in state to identify the user
    # who wants to connect his google/youtube account
    auth_url = get_google_auth_url(current_user.id)
    return RedirectResponse(auth_url)



@router.get("/connect/tiktok")
async def tiktok_connect(current_user: dict = Depends(get_current_user)):
    """Redirect to Tiktok's OAuth login."""
    # Pass user id in state to identify the user
    # who wants to connect his tiktok account
    print("User want to connect tiktok account")
    auth_url = get_tiktok_auth_url(current_user.id)
    return RedirectResponse(auth_url)


@router.post("/disconnect/{platform}")
async def remove_connected_account(platform:str,current_user:dict= Depends(get_current_user)):
    user_credentials = current_user.credentials
    #Remove the platform eg tiktok or google from the credentials 
    filtered_credentials = {
        key: value
        for key, value in user_credentials.items()
        if key != platform
    }
    
   
    # Now save the rest of the credentials in user credentials
    try:
        await prisma.user.update(
            where={
                "id":current_user.id
            },
            data={
                "credentials":json.dumps(filtered_credentials)
            }
        )
        return Response("Success",200)
    except Exception as e:
        print(e)
        return Response("Error",400)

