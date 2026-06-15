import json
from fastapi.responses import RedirectResponse , Response
import requests
from utils import create_access_token, exchange_code_for_tokens
from db import prisma
import datetime
from prisma import errors
import os


def set_user_credentials(user, token_data,provider:str) -> any:
    cre_data = {provider: token_data}
    cre_json = json.dumps(cre_data)
    if user is not None:
        if user.credentials is not None:
            # Merge or update TikTok credentials in user's credentials
            user_creds = user.credentials
            if provider in user_creds:
                user_creds[provider] = json.dumps(token_data)
            else:
                user_creds = {**user.credentials, provider:json.dumps(token_data)}
            # Save the updated credentials back to the user
            cre_json = json.dumps(user_creds)
    return cre_json         



async def authGoogle (code:str,state:any = None): 
    """ For login and signUp users"""
    credentials = exchange_code_for_tokens(code)
    access_token = credentials.get("access_token") 

     # in state we have user id . It means user want to connect his youtube/google, account not to login
    if state is not None and state != "None":
        user = await prisma.user.find_unique(
            where={
                "id":int(state)
            }
        )
        if user is not None:
            # Connect Google account to existing user
            # Credentials data save in user property
            cre_json = set_user_credentials(user, credentials,"google")     
            await prisma.user.update(
                where={"id": user.id},
                data={
                    "credentials":  cre_json,
                }
            )
            return RedirectResponse("/dashboard", status_code=200, headers={"message":"Youtube account connected successfully"})
        else:
            print("User want to login")

    # Google UserInfo API URL
    user_info_url = "https://www.googleapis.com/oauth2/v3/userinfo"

    # Set up headers with the Bearer token
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # Send a GET request to the Google UserInfo endpoint
    response = requests.get(user_info_url, headers=headers)
    if response.status_code != 200:
        return  RedirectResponse("/error",400,{"message":"No user info retrieved please try again"})

    # Parse the response JSON to get user info
    user_info = response.json()

    jwt_token = create_access_token(data={"sub": user_info["email"]})
   



    try:

        user = await prisma.user.find_first(where={"email":user_info["email"]})


        # Credentials data save in user property
        cre_json = set_user_credentials(user, credentials,"google")   

        if user is None :
            await prisma.user.create(
                data={
                    "fullname":user_info["name"],
                    "email":user_info["email"],
                    "avator":user_info["picture"],
                    "is_em_verif":user_info["email_verified"],
                    "password":"Not set",
                    "updatedAt": datetime.datetime.utcnow(),
                    "credentials": cre_json,
                     "usage":{
                        "create":{
                            "uploadsToday":0,
                        }
                    },
                    "plan":{
                        "create":{
                            "name":"Free",
                        }
                    },
                    "userConfig":{
                        "create":{
                            "theme":"dark",
                            "language":"en"
                        }
                    }
                }
            )
            print("user created")
        else :
            # Update user credentials if they already exist 
            await prisma.user.update(
                where={"email": user_info["email"]},
                data={
                    "credentials":  cre_json,
                }
            )
            print("user updated")
        # redirect_url = getenv("FRONTEND_URL")
        # res = RedirectResponse(redirect_url,301,headers={"token": jwt_token})
        res = RedirectResponse("/dashboard", status_code=303)
        res.set_cookie(
            key="token",
            value=jwt_token,
            httponly=True,
            max_age=7 * 24 * 60 * 60,  # 7 days in seconds
            expires=7 * 24 * 60 * 60
        )
        return res

    except errors.ClientNotConnectedError as e:
        return Response(f"Error : {e} ",500)
 
  

async def authTiktok (code:str,state:any = None):
    """ For login and signUp users via TikTok """
    # Exchange the code for access token
    tiktok_token_url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
        'Accept'       :'application/json',
    }
    data = {
        "client_key": os.getenv("TIKTOK_CLIENT_ID"),
        "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": os.getenv("TIKTOK_REDIRECT_URI")
    }
    token_response = requests.post(tiktok_token_url, headers=headers, data=data)

    if token_response.status_code != 200:
        return RedirectResponse("/error", 400, {"message": "Failed to retrieve access token from TikTok"})

    token_data = token_response.json()
    access_token = token_data.get("access_token")
    open_id = token_data.get("open_id") 

    print("token_data",token_data)

     # in state we have user id . It means user want to connect his tiktok account not to login
    if state is not None and state != "None": 
        user = await prisma.user.find_unique(
            where={
                "id":int(state)
            }
        )
        if user is not None:
            # Connect TikTok account to existing user
            # Credentials data save in user property
            cre_json = set_user_credentials(user, token_data,"tiktok")     
            await prisma.user.update(
                where={"id": user.id},
                data={
                    "credentials":  cre_json,
                }
            )

             
            return RedirectResponse("/dashboard", status_code=200, headers={"message":"Tiktok account connected successfully"})
        else:
            print("User want to login")

    print("User want to login....")
            

    # Get user info from TikTok
    get_fields = "open_id,avatar_url,display_name,username"
    user_info_url = f"https://open.tiktokapis.com/v2/user/info/?fields={get_fields}"
    headers= {
        "Authorization":f"Bearer {access_token}"
    }

    user_info_response = requests.get(user_info_url, headers=headers)
    if user_info_response.status_code != 200:
        return RedirectResponse("/error", 400, {"message": "Failed to retrieve user info from TikTok"})

    user_info = user_info_response.json().get("data", {}).get("user")
    open_id = user_info.get("open_id")

   

    jwt_token = create_access_token(data={"sub": open_id + "@tiktok.com"})
    try: 
        user = await prisma.user.find_first(
            where={
                "email": open_id + "@tiktok.com"
                }
            )

        # Credentials data save in user property
        cre_json = set_user_credentials(user, token_data,"tiktok")     

        if user is None:
            await prisma.user.create(
                data={
                    "fullname": user_info.get("display_name", "TikTok User"),
                    "email": open_id + "@tiktok.com",
                    "avator": user_info.get("avatar_url", ""),
                    "is_em_verif": True,  # TikTok does not provide email verification status
                    "password": "Not set",
                    "updatedAt": datetime.datetime.utcnow(),
                    "credentials": cre_json,
                    "usage":{
                        "create":{
                            "uploadsToday":0,
                        }
                    },
                    "plan":{
                        "create":{
                            "name":"Free",
                        }
                    },
                    "userConfig":{
                        "create":{
                            "theme":"dark",
                            "language":"en"
                        }
                    }
                }
            )

            print("user created")
        else:
            # Update user credentials if they already exist 

            await prisma.user.update(
                where={"email": open_id + "@tiktok.com"},
                data={
                    "credentials":  cre_json,
                }
            )
            print("user updated")
        res = RedirectResponse("/dashboard", status_code=303)
        res.set_cookie( 
            key="token",
            value=jwt_token,
            httponly=True,
            max_age=7 * 24 * 60 * 60,  # 7 days in seconds
            expires=7 * 24 * 60 * 60
        )
        return res

    except errors.ClientNotConnectedError as e:
        print("Error in authTiktok:", e)
        return Response(f"Error : {e} ", 500)