import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv
import os
from typing import Optional
from models.video import create_video
from wroker import generate_caption_htags
from db import prisma
from controller.copyright_transformer import build_transformations_array
import json
load_dotenv()
import asyncio
import httpx
from controller.video_downloader import download_video

tempFiles = []

WORKER_SERVICE_URL = os.getenv("WORKER_SERVICE_URL", "").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")


def deserialize_and_map_dynamic_edits(dynamic_edits_config):
    """
    Deserialize JSON strings from database and map frontend field names to transformer field names.
    Frontend sends: {speed, frequency, mute, text, color, opacity, position, yGap, vignette, noise}
    Transformer expects: {audio_speed, audio_frequency, mute_audio, text_content, text_color, text_opacity, text_gravity, text_y_gap, vignette_strength, noise_strength}
    """
    if not dynamic_edits_config:
        return None

    try:
        # Deserialize JSON strings from database
        audio_controls = dynamic_edits_config.get("audioControls", {})
        if isinstance(audio_controls, str):
            audio_controls = json.loads(audio_controls)

        text_overlay_controls = dynamic_edits_config.get("textOverlayControls", {})
        if isinstance(text_overlay_controls, str):
            text_overlay_controls = json.loads(text_overlay_controls)

        pixel_distortion_controls = dynamic_edits_config.get("pixelDistortionControls", {})
        if isinstance(pixel_distortion_controls, str):
            pixel_distortion_controls = json.loads(pixel_distortion_controls)

        # Map frontend field names to transformer field names
        mapped_config = {
            "audioControls": {
                "audio_speed": audio_controls.get("speed"),
                "audio_frequency": audio_controls.get("frequency"),
                "mute_audio": audio_controls.get("mute", False)
            },
            "textOverlayControls": {
                "text_content": text_overlay_controls.get("text", ""),
                "text_color": text_overlay_controls.get("color", "#F97316"),
                "text_opacity": text_overlay_controls.get("opacity", 60),
                "text_gravity": text_overlay_controls.get("position", "south"),
                "text_y_gap": text_overlay_controls.get("yGap", 120)
            },
            "pixelDistortionControls": {
                "vignette_strength": pixel_distortion_controls.get("vignette", 0),
                "noise_strength": pixel_distortion_controls.get("noise", 0)
            }
        }

        return mapped_config
    except Exception as e:
        print(f"Error deserializing dynamic edits: {str(e)}")
        return None


 
async def upload_to_cloudinary(filePath: str, userId: any, upload_targets: any, video_id=None, title=None):
    """This will upload the asset to cloudinary and create video in database will save the details needed"""

    # Fetch user's dynamic edit configuration
    user = await prisma.user.find_unique(
        where={"id": userId},
        include={"dynamicEdits": True}
    )

    # Get dynamic edits config if exists
    dynamic_edits_config = None
    if user and user.dynamicEdits:
        raw_config = {
            "audioControls": user.dynamicEdits.audioControls,
            "textOverlayControls": user.dynamicEdits.textOverlayControls,
            "pixelDistortionControls": user.dynamicEdits.pixelDistortionControls,
        }
        # Deserialize and map field names
        dynamic_edits_config = deserialize_and_map_dynamic_edits(raw_config)

    # Build transformations from user config or use defaults
    transformations_list = build_transformations_array(userId, dynamic_edits_config)

    upload_response = cloudinary.uploader.upload(
        filePath,
        resource_type="video",
        eager_async=True,
        transformation=transformations_list,
        folder="social-bot-videos",
        overwrite=True,
    )

    print("Uploaded video to Cloudinary:", upload_response['secure_url'])
    video = await create_video(userId, upload_response["secure_url"], upload_response["asset_id"], title, upload_targets, video_id)
    userPlan = video.user.plan.type

    # Add a worker that generate Caption and #tags for video.
    print("Assigned to asyncio task to generate #tags and captions")
    asyncio.create_task(
        generate_caption_htags(
            video.id,
            userPlan,
            title,
            "trends",
            None
        )
    )



async def download_video_async(video_url: str) -> dict:
    return await asyncio.to_thread(download_video, video_url)

class FromPlatform:
    def __init__(self, video_url, user_id: int, upload_targets=["Youtube"], video_id=None):
        self.user_id = user_id,
        self.video_url = video_url
        self.video_id = video_id
        self.upload_targets = upload_targets
        self.title = "Untitled video"


    async def _extract_and_upload(self, platform: Optional[str] = None):
        """Ask the worker for a direct media URL, then upload that URL to Cloudinary."""
        try:
            result = await download_video_async(self.video_url)

            if not result["filepath"]:
                err = result.get("error") or "Worker did not return a media URL"
                print(f"Worker extract failed: {err}")
                return {"Error": err}

            self.title = result["title"] or self.title
            direct_url = result["filepath"]

            print("userid", self.user_id[0])
            print("upload_targets", self.upload_targets)
            print("Resolved direct URL via worker service")

            try:
                # Cloudinary fetches  local /tmp binary on Vercel
                await upload_to_cloudinary(
                    direct_url,
                    self.user_id[0],
                    self.upload_targets,
                    self.video_id,
                    self.title,
                )
    
            finally:
                if os.path.exists(local_path):
                    os.remove(local_path)

            await prisma.usage.update(
                where={
                    "userId": self.user_id[0]
                },
                data={
                    "scheduledToday": {
                        "increment": 1
                    },
                }
            )
            return {"success": "Video uploaded to Cloudinary success"}

        except httpx.HTTPStatusError as e:
            # Prefer FastAPI's JSON {"detail": "..."} so yt-dlp errors are readable
            try:
                detail = e.response.json().get("detail", e.response.text)
            except Exception:
                detail = e.response.text
            print(f"Worker HTTP error: {e.response.status_code} {detail}")
            return {"Error": f"Worker error {e.response.status_code}: {detail}"}
        except Exception as e:
            print(f"Error : {e}")
            return {"Error": f"{e}"}

    async def tiktok(self):
        return await self._extract_and_upload(platform="tiktok")

    async def facebook(self):
        return await self._extract_and_upload(platform="facebook")

    async def youtube(self):
        return await self._extract_and_upload(platform="youtube")

    async def instagram(self):
        return await self._extract_and_upload(platform="instagram")

    async def pexels(self):
        print("Pexels video loaded")
        await upload_to_cloudinary(self.video_url, self.user_id[0], self.upload_targets)
        return {"success": "Video uploaded to Cloudinary success"}

    def __getitem__(self, platform):
        methods = {
            "tiktok": self.tiktok,
            "facebook": self.facebook,
            "youtube": self.youtube,
            "instagram": self.instagram,
            "pexels": self.pexels,
        }
        if platform not in methods:
            raise KeyError(f"Platform '{platform}' is not supported.")
        return methods[platform]()
