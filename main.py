import json
from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates 
from db import prisma 
from dotenv import load_dotenv
from lib.depends import get_current_user
from lib.socket import ConnectionManager
from routers.dashboard import router as dashboard_routes 
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from routers.test import router as test_routes
from routers.mvp.v1.generate import router as mvp_routes
from routers.checkout import router as checkout_routes
from routers.api.schedule import router as schedule_routes
from routers.api.user import router as user_routes
from routers.ai_discovery import router as ai_discovery_routes
from routers.auth import router as auth_routes
load_dotenv() 
app = FastAPI()
 
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(dashboard_routes) 
app.include_router(test_routes) 
app.include_router(mvp_routes) 
app.include_router(checkout_routes)
app.include_router(schedule_routes)
app.include_router(user_routes) 
app.include_router(ai_discovery_routes) 
app.include_router(auth_routes) 
# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Render templates
templates = Jinja2Templates(directory="templates")

# WebSocket manager
manager = ConnectionManager()
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Optionally handle incoming messages here
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)

# Utility functions to use elsewhere in your app
async def send_status(user_id: str, status: str):
    await manager.send_personal_message(status, user_id)

async def send_notification(user_id: str, notification: str):
    await manager.send_personal_message(notification, user_id)
    
@app.get("/send-notify")
async def send_notify():
    await send_status("user_id_123", "ready")
    return {"message": "Notification sent"}
    
# Home page
@app.get("/",response_class=HTMLResponse)
async def home(request:Request):
    return templates.TemplateResponse('home.html',{"request":request})


@app.get("/about",response_class=HTMLResponse)
async def privacy_page(request:Request):
    return templates.TemplateResponse(
            "about.html", 
            {
                "request":request
            }
    )
@app.get("/privacy",response_class=HTMLResponse)
async def privacy_page(request:Request):
    return templates.TemplateResponse(
            "privacy.html", 
            {
                "request":request
            }
    )

@app.get("/contact",response_class=HTMLResponse)
async def privacy_page(request:Request):
    return templates.TemplateResponse(
            "contact.html", 
            {
                "request":request
            }
    )
@app.get("/pricing",response_class=HTMLResponse)
async def privacy_page(request:Request):
    return templates.TemplateResponse(
            "pricing.html", 
            {
                "request":request
            }
    )

@app.get("/terms",response_class=HTMLResponse)
async def privacy_page(request:Request):
    return templates.TemplateResponse(
            "terms.html", 
            {
                "request":request
            }
    )


# Login page
@app.get("/login", response_class=HTMLResponse)
async def read_root(request: Request):
    # Render the "index.html" template 
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/test/user")
async def logout():
    user = await prisma.user.find_first(
       where={
           "email":"afnanahhad@gmail.com"
       }
   )
    credentials = user.credentials
    cre_obj = json.loads(credentials["tiktok"])
   
    return cre_obj

  
@app.on_event("startup")
async def start_scheduler():
    pass
    # await prisma.connect()

@app.on_event("shutdown")
async def shutdown_scheduler():
    await prisma.disconnect()

 