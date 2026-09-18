"""
Standalone download-worker service.

Exposes POST /extract — uses yt-dlp with download=False to resolve
direct streaming/download URLs (no binary written to disk).
"""

from __future__ import annotations

import logging
import os
import random
from typing import Any, Optional
from urllib.parse import quote

import yt_dlp
from dotenv import load_dotenv
from pydantic import BaseModel, HttpUrl
import uuid
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask



load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("download-worker")

 
# Opt-in only — binding source_address=0.0.0.0 breaks outbound networking in Docker.
FORCE_IPV4 = os.getenv("FORCE_IPV4", "false").lower() in ("1", "true", "yes")

# Oxylabs-style rotating entry points (host:port + country).
# Override via PROXY_ENTRYPOINTS as comma-separated "host:port:COUNTRY" values.
_DEFAULT_ENTRYPOINTS = [
    "dc.oxylabs.io:8001:US",
    "dc.oxylabs.io:8002:US",
    "dc.oxylabs.io:8003:US",
    "dc.oxylabs.io:8004:US",
    "dc.oxylabs.io:8005:US",
]


def _parse_entrypoints() -> list[dict[str, Any]]:
    raw = os.getenv("PROXY_ENTRYPOINTS", "")
    sources = [s.strip() for s in raw.split(",") if s.strip()] or _DEFAULT_ENTRYPOINTS
    parsed: list[dict[str, Any]] = []
    for item in sources:
        parts = item.split(":")
        if len(parts) < 3:
            continue
        host, port, country = parts[0], parts[1], parts[2]
        parsed.append({"host": host, "port": port, "country": country})
    return parsed or [
        {"host": "dc.oxylabs.io", "port": "8001", "country": "US"},
    ]


PROXY_ENTRYPOINTS = _parse_entrypoints()


 
def pick_proxy_url() -> Optional[str]:
    """Build a rotating residential/datacenter proxy URL, or None if credentials are missing."""
    if not PROXY_USERNAME or not PROXY_PASSWORD:
        logger.warning("PROXY_USERNAME/PROXY_PASSWORD not set — extracting without proxy")
        return None
    entry = random.choice(PROXY_ENTRYPOINTS)
    # Oxylabs DC proxies are HTTP CONNECT endpoints. Default to http:// so yt-dlp's
    # urllib backend works. https:// requires requests/curl_cffi at runtime.
    scheme = os.getenv("PROXY_SCHEME", "http").strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    if scheme == "https" and not _has_https_proxy_backend():
        logger.warning(
            "PROXY_SCHEME=https but requests/curl_cffi are not importable; falling back to http://"
        )
        scheme = "http"
    user = f"user-{PROXY_USERNAME}-country-{entry['country']}"
    password = quote(PROXY_PASSWORD, safe="")
    proxy_url = f"{scheme}://{user}:{password}@{entry['host']}:{entry['port']}"
    logger.info(
        "Using %s proxy %s:%s country=%s",
        scheme,
        entry["host"],
        entry["port"],
        entry["country"],
    )
    return proxy_url


def build_ydl_opts(proxy_url: Optional[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        # Prefer a single pre-muxed file so extract_info yields one direct URL.
        "format": "b[ext=mp4]/best[ext=mp4]/best/bestvideo*+bestaudio/best",
        "quiet": False,
        "no_warnings": False,
        "verbose": False,
        "noplaylist": True,
        # Disable local caching
        "cachedir": False,
        # Keep any incidental temp paths under /tmp
        "paths": {"home": "/tmp", "temp": "/tmp"},
        "outtmpl": "/tmp/%(id)s.%(ext)s",
        "extractor_args": {
            "youtube": {
                # android/ios APIs are frequently blocked; web clients are more reliable
                "player_client": ["tv", "web", "mweb", "android"],
            }
        },
        "socket_timeout": 30,
        "retries": 3,
    }
    if FORCE_IPV4:
        # yt-dlp --force-ipv4 — avoid enabling inside Docker unless needed
        opts["source_address"] = "0.0.0.0"
    if proxy_url:
        opts["proxy"] = proxy_url
    return opts

 

 

def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _safe_header(value: Optional[str]) -> str:
    """HTTP headers must be latin-1; strip/escape anything that isn't."""
    if not value:
        return ""
    return value.encode("ascii", "ignore").decode("ascii")[:200]


logger = logging.getLogger("video-downloader")


class VideoDownloadError(Exception):
    """Raised when yt-dlp fails to download the requested video."""
    pass


def download_video(video_url: str) -> dict:
    """
    Downloads a video via yt-dlp into /tmp and returns its local path + metadata.
    Caller is responsible for deleting the file after use.
    """
    proxy_url = pick_proxy_url()
    ydl_opts = build_ydl_opts(proxy_url)
    file_id = str(uuid.uuid4())
    ydl_opts["outtmpl"] = f"/tmp/{file_id}.%(ext)s"

    logger.info("Downloading url=%s proxy=%s", video_url, bool(proxy_url))

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next((e for e in info["entries"] if e), None) or info
            filepath = ydl.prepare_filename(info)
    except yt_dlp.utils.DownloadError as exc:
        logger.exception("Download failed for %s", video_url)
        raise VideoDownloadError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected failure for %s", video_url)
        raise VideoDownloadError(str(exc)) from exc

    if not os.path.exists(filepath):
        raise VideoDownloadError("Download completed but file missing on disk")

    ext = info.get("ext") or os.path.splitext(filepath)[1].lstrip(".")

    return {
        "filepath": filepath,
        "title": info.get("title"),
        "ext": ext,
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "webpage_url": info.get("webpage_url") or video_url,
    }