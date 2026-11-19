import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv
import os
from typing import Optional
from models.video import create_video, create_queued_video, assert_can_schedule, QuotaExceededError
from wroker import generate_caption_htags
from db import prisma
from controller.copyright_transformer import build_transformations_array
from controller.cloudinary_transformer import build_playground_transformation_chain
import json
load_dotenv()
import asyncio
from controller.video_downloader import download_video
from controller.worker_client import dispatch_download, last_dispatch_error, worker_enabled, worker_required
from utils import queued_title

tempFiles = []


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


 
async def user_transformations(userId) -> list:
    """Cloudinary transformation chain applied to EVERY upload.

    The Playground is now a page of default settings, not a per-video tool:
    whatever the user saved there is baked into each video right before it is
    uploaded (worker or in-process alike). Accounts that never opened the
    Playground keep the legacy Anti-Copyright Settings behaviour.
    """
    user = await prisma.user.find_unique(
        where={"id": userId},
        include={"dynamicEdits": True}
    )

    raw_playground = None
    dynamic_edits_config = None
    if user and user.dynamicEdits:
        stored = user.dynamicEdits.playgroundTransformations
        if isinstance(stored, str):
            try:
                stored = json.loads(stored or "{}")
            except (TypeError, ValueError):
                stored = {}
        if isinstance(stored, dict) and stored:
            raw_playground = stored
        else:
            dynamic_edits_config = deserialize_and_map_dynamic_edits({
                "audioControls": user.dynamicEdits.audioControls,
                "textOverlayControls": user.dynamicEdits.textOverlayControls,
                "pixelDistortionControls": user.dynamicEdits.pixelDistortionControls,
            })

    if raw_playground is not None:
        if not raw_playground.get("enabled", True):
            # The user deliberately turned every tweak off: upload untouched.
            return []
        return build_playground_transformation_chain(raw_playground)

    return build_transformations_array(userId, dynamic_edits_config)


async def upload_to_cloudinary(
    filePath: str,
    userId: any,
    upload_targets: any,
    video_id=None,
    title=None,
    scraped_from: str | None = None,
):
    """This will upload the asset to cloudinary and create video in database will save the details needed"""

    # Fail fast: never spend a Cloudinary upload when the queue is already full.
    await assert_can_schedule(userId)

    # None (not []) when the user turned every tweak off: an empty list is not
    # a valid Cloudinary transformation payload.
    transformations_list = await user_transformations(userId) or None

    upload_response = await asyncio.to_thread(
        cloudinary.uploader.upload,
        filePath,
        resource_type="video",
        eager_async=True,
        transformation=transformations_list,
        folder="social-bot-videos",
        overwrite=True,
    )

    print("Uploaded video to Cloudinary:", upload_response['secure_url'])
    video = await create_video(
        userId,
        upload_response["secure_url"],
        # public_id (not asset_id) is what Cloudinary requires to delete the asset later
        upload_response.get("public_id") or upload_response["asset_id"],
        title,
        upload_targets,
        video_id,
        scraped_from=scraped_from,
    )
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
        self.user_id = user_id
        self.video_url = video_url
        self.video_id = video_id
        self.upload_targets = upload_targets
        self.title = "Untitled video"

    async def _extract_and_upload(self, platform: Optional[str] = None):
        """Download the source media locally, then upload it to Cloudinary."""

        # Check quota before downloading: rejecting after a full video download
        # wastes bandwidth and blocks the request for nothing.
        try:
            await assert_can_schedule(self.user_id)
        except QuotaExceededError as e:
            print(f"Quota check failed: {e}")
            return {"Error": str(e)}

        # Hand the job to the worker fleet when one is configured. The web host
        # may lack Deno/ffmpeg and have a timeout shorter than the download.
        if worker_enabled():
            dispatched = await dispatch_download(
                user_id=self.user_id,
                video_url=self.video_url,
                platform=platform or "",
                title=None,  # let the worker keep the real extracted title
                external_id=self.video_id,
                upload_destinations=self.upload_targets,
                transformation=await user_transformations(self.user_id),
            )
            if dispatched and dispatched.get("jobId"):
                try:
                    await create_queued_video(
                        user_id=self.user_id,
                        job_id=dispatched["jobId"],
                        title=queued_title(platform or "", self.video_url),
                        upload_destinations=self.upload_targets,
                        platform=platform,
                    )
                except QuotaExceededError as e:
                    print(f"Quota check failed: {e}")
                    return {"Error": str(e)}
                return {"success": "Download queued on worker", "queued": True}

            if worker_required():
                # On a serverless host the in-process fallback cannot finish.
                return {"Error": f"Download worker unreachable: {last_dispatch_error() or 'unknown error'}"}
            print(f"Worker dispatch failed ({last_dispatch_error()}); downloading in-process")

        try:
            result = await download_video_async(self.video_url)

            if not result["filepath"]:
                err = result.get("error") or "Downloader did not return a file path"
                print(f"Download failed: {err}")
                return {"Error": err}

            self.title = result["title"] or self.title
            direct_url = result["filepath"]

            print("userid", self.user_id)
            print("upload_targets", self.upload_targets)
            print("Downloaded source media")

            try:
                await upload_to_cloudinary(
                    direct_url,
                    self.user_id,
                    self.upload_targets,
                    self.video_id,
                    self.title,
                    scraped_from=platform,
                )
            finally:
                # Always clean up the temp file, even when the upload fails.
                if os.path.exists(direct_url):
                    os.remove(direct_url)

            # scheduledToday is incremented once, inside create_video.
            return {"success": "Video uploaded to Cloudinary success"}

        except QuotaExceededError as e:
            print(f"Quota check failed: {e}")
            return {"Error": str(e)}
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
        try:
            await upload_to_cloudinary(
                self.video_url,
                self.user_id,
                self.upload_targets,
                scraped_from="pexels",
            )
        except QuotaExceededError as e:
            print(f"Quota check failed: {e}")
            return {"Error": str(e)}
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

    async def run(self, platform):
        """Awaitable entry point usable as a BackgroundTasks callable."""
        return await self[platform]
