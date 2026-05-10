import os
import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()  # load PADDLE_API_KEY from .env

PADDLE_API_KEY = os.getenv("PADDLE_API_KEY")
PADDLE_API_URL = "https://sandbox-api.paddle.com"

router = APIRouter(prefix="/checkout", tags=["checkout"])

class CheckoutRequest(BaseModel):
    price_id: str
    customer_email: str

# ✅ Create a checkout session
@router.post("/create")
async def create_checkout(req: CheckoutRequest):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PADDLE_API_URL}/checkouts",
            headers={
                "Authorization": f"Bearer {PADDLE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "customer": {"email": req.customer_email},
                "items": [{"price_id": req.price_id, "quantity": 1}],
                "success_url": "https://yourapp.com/success",
                "cancel_url": "https://yourapp.com/cancel",
            },
        )

    return response.json()  # contains checkout_url


# ✅ Webhook to listen for Paddle events
@router.post("/webhook")
async def paddle_webhook(request: Request):
    payload = await request.json()
    # TODO: verify webhook signature here using Paddle’s public key
    # See: https://developer.paddle.com/webhooks/signatures

    event_type = payload.get("event_type")
    data = payload.get("data", {})

    # Handle subscription events
    if event_type == "subscription.created":
        print("✅ Subscription Created:", data)
    elif event_type == "subscription.updated":
        print("🔄 Subscription Updated:", data)
    elif event_type == "subscription.canceled":
        print("❌ Subscription Canceled:", data)

    return {"status": "ok"}
