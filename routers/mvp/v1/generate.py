from fastapi import APIRouter, Depends
import json
import os


router = APIRouter(prefix="/mvp/v1", tags=["mvp_agent"])


@router.post("/generate")
async def generate(payload: dict):
    # Load config
    config_path = os.path.join(os.path.dirname(__file__), "platform-config.json")
    with open(config_path) as f:
        config = json.load(f)


    result = "await orchestrate(payload, config)"

    return result