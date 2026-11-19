"""
yt-dlp download helper shared by the API and the standalone download worker.

YouTube needs an external JS runtime (Deno) to solve its JS challenges, and
YouTube / Instagram / Facebook authenticate the session rather than the IP — so a
Netscape cookies.txt per platform is resolved before every attempt.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
from typing import Any, Optional
from urllib.parse import quote, urlparse

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

PROXY_USERNAME=os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD=os.getenv("PROXY_PASSWORD", "")
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


 
# ── Cookies, ffmpeg and per-platform egress policy ────────────────────────────
# Platforms authenticate the *session*; a proxy only changes IP reputation and
# cannot satisfy "sign in to confirm you're not a bot".
COOKIE_ENV = {
    "youtube":   "COOKIES_YOUTUBE_B64",
    "instagram": "COOKIES_INSTAGRAM_B64",
    "facebook":  "COOKIES_FACEBOOK_B64",
    "tiktok":    "COOKIES_TIKTOK_B64",
}
COOKIE_DIR = os.getenv("COOKIES_DIR") or os.path.join(tempfile.gettempdir(), "yt-cookies")
_cookie_paths: dict[str, str] = {}

COOKIE_ERROR_HINTS = (
    "sign in to confirm",
    "login required",
    "rate-limit reached",
    "requested content is not available",
    "cookies",
    "requires authentication",
    "private video",
    "http error 401",
    "http error 403",
)

# yt-dlp solves YouTube's signature / n-challenges in an external JS runtime and
# only enables Deno by default. Node >= 22 and Bun >= 1.2.11 work equally well.
JS_RUNTIME_NAMES = [
    name.strip().lower()
    for name in os.getenv("YTDLP_JS_RUNTIMES", "deno,node,bun").split(",")
    if name.strip()
]

JS_RUNTIME_ERROR_HINTS = (
    "signature solving failed",
    "challenge solving failed",
    "javascript runtime",
    "js runtime",
    "challenge solver",
)

# A proxy that answers 407 will never work for this process; stop retrying it.
PROXY_AUTH_ERROR_HINTS = (
    "tunnel connection failed",
    "proxy authentication required",
    "407 unauthorized",
    "proxyerror",
)

_js_runtime_warned = False

# YouTube scores every session; "Sign in to confirm you're not a bot" is often a
# transient verdict that clears on a fresh anonymous session a little later.
BOT_CHECK_HINTS = ("sign in to confirm", "not a bot")


def _env_number(name: str, default, cast):
    """Numeric env var that tolerates an empty or malformed value."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using %s", name, raw, default)
        return default


BOT_CHECK_RETRIES = max(0, _env_number("BOT_CHECK_RETRIES", 1, int))
BOT_CHECK_DELAY_SECONDS = _env_number("BOT_CHECK_DELAY", 30.0, float)

# Comma-separated platform keys that must never be routed through the proxy.
_NO_PROXY = {
    p.strip().lower()
    for p in os.getenv("PROXY_DISABLE_PLATFORMS", "").split(",")
    if p.strip()
}


def _has_https_proxy_backend() -> bool:
    """yt-dlp can speak https:// proxies when requests/curl_cffi is importable."""
    try:
        import requests  # noqa: F401

        return True
    except ImportError:
        return False


def platform_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "youtu" in host:
        return "youtube"
    if "instagram" in host:
        return "instagram"
    if "facebook" in host or host == "fb.watch":
        return "facebook"
    if "tiktok" in host:
        return "tiktok"
    return ""


def cookie_file_for(platform: str) -> Optional[str]:
    """Write COOKIES_<PLATFORM>_B64 to a Netscape cookie file once per process."""
    env_name = COOKIE_ENV.get(platform)
    if not env_name:
        return None

    # A plain file on disk wins: handy locally, and supports mounted secrets.
    file_env = env_name.replace("_B64", "_FILE")
    explicit = os.getenv(file_env, "").strip()
    if explicit:
        if os.path.exists(explicit):
            _cookie_paths[platform] = explicit
            return explicit
        logger.warning("%s points to a missing file: %s", file_env, explicit)

    cached = _cookie_paths.get(platform)
    if cached and os.path.exists(cached):
        return cached

    blob = os.getenv(env_name, "").strip()
    if not blob:
        return None

    payload = blob.encode()
    try:
        decoded = base64.b64decode(blob, validate=True)
        # Netscape exports start with "# Netscape HTTP Cookie File" or hold tabs
        if decoded.startswith(b"#") or b"\t" in decoded:
            payload = decoded
    except Exception:
        pass

    os.makedirs(COOKIE_DIR, exist_ok=True)
    path = os.path.join(COOKIE_DIR, f"{platform}.txt")
    with open(path, "wb") as fh:
        fh.write(payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    _cookie_paths[platform] = path
    return path


def ffmpeg_path() -> Optional[str]:
    """ffmpeg is required to mux separate video/audio streams."""
    explicit = os.getenv("FFMPEG_LOCATION", "").strip()
    if explicit:
        return explicit
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def js_runtime_status() -> dict[str, Optional[str]]:
    """Version of each JS runtime yt-dlp could use, or None when not installed."""
    status: dict[str, Optional[str]] = {}
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if not path:
            status[name] = None
            continue
        try:
            proc = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=10
            )
            first = (proc.stdout or proc.stderr).strip().splitlines()
            status[name] = (first[0][:30] if first else "present") or "present"
        except Exception:  # noqa: BLE001
            status[name] = "present"
    return status


def _js_runtime_summary() -> str:
    status = js_runtime_status()
    return " ".join(f"{name}={value or 'no'}" for name, value in status.items())


def _warn_if_no_js_runtime(platform: str) -> None:
    global _js_runtime_warned
    if _js_runtime_warned or platform != "youtube":
        return
    _js_runtime_warned = True
    if not any(js_runtime_status().values()):
        logger.warning(
            "No JavaScript runtime on PATH (deno/node/bun): YouTube signature solving "
            "will fail. Install Deno, or Node >= 22, or set YTDLP_JS_RUNTIMES."
        )


def _looks_like_proxy_auth_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in PROXY_AUTH_ERROR_HINTS)


def _is_bot_check_error(text: Optional[str]) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in BOT_CHECK_HINTS)


def proxy_for(platform: str) -> Optional[str]:
    if platform in _NO_PROXY:
        logger.info("Proxy disabled for platform=%s", platform)
        return None
    return pick_proxy_url()


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
    # Oxylabs datacenter proxies authenticate as `user-<username>` (country comes
    # from the port); residential ones as `customer-<username>-country-<cc>`.
    default_format = (
        "user-{username}"
        if entry["host"].startswith("dc.")
        else "customer-{username}-country-{country}"
    )
    user_format = os.getenv("PROXY_USER_FORMAT", "").strip() or default_format
    try:
        user = user_format.format(username=PROXY_USERNAME, country=entry["country"])
    except (KeyError, IndexError, ValueError):
        logger.warning("PROXY_USER_FORMAT is invalid — falling back to user-{username}")
        user = f"user-{PROXY_USERNAME}"
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


def download_dir() -> str:
    """Directory used for temporary downloads (override with DOWNLOAD_DIR)."""
    path = os.getenv("DOWNLOAD_DIR") or tempfile.gettempdir()
    os.makedirs(path, exist_ok=True)
    return path


def build_ydl_opts(
    proxy_url: Optional[str],
    platform: str = "",
    cookie_file: Optional[str] = None,
) -> dict[str, Any]:
    target_dir = download_dir()
    opts: dict[str, Any] = {
        # Prefer a single pre-muxed file so extract_info yields one direct URL.
        "format": "b[ext=mp4]/best[ext=mp4]/bestvideo*+bestaudio/best",
        "quiet": False,
        "no_warnings": False,
        "verbose": False,
        "noplaylist": True,
        # Disable local caching
        "cachedir": False,
        # outtmpl is always absolute, so yt-dlp ignores "paths" anyway — it only
        # produced a warning. Temp files land next to the output file.
        "outtmpl": os.path.join(target_dir, "%(id)s.%(ext)s"),
        # No ANSI escapes in captured error messages / logs
        "color": "no_color",
        "extractor_args": {
            "youtube": {
                # tv/web_safari outlive android/ios, but still need Deno + PO token
                "player_client": ["tv", "web_safari", "web", "mweb"],
            }
        },
        "socket_timeout": 30,
        "retries": 3,
        # Bursty traffic is what triggers the bot check in the first place
        "sleep_interval_requests": float(os.getenv("YTDLP_SLEEP_REQUESTS", "1")),
        "concurrent_fragment_downloads": 1,
    }

    if JS_RUNTIME_NAMES:
        # Enables Node/Bun as a fallback so a Deno-less host still solves challenges
        opts["js_runtimes"] = {name: {} for name in JS_RUNTIME_NAMES}

    ffmpeg = ffmpeg_path()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if proxy_url:
        opts["proxy"] = proxy_url
    if FORCE_IPV4:
        # yt-dlp --force-ipv4 — avoid enabling inside Docker unless needed
        opts["source_address"] = "0.0.0.0"

    # Escape hatch for tuning without a redeploy, e.g.
    # YTDLP_EXTRA_OPTS='{"cookiesfrombrowser": ["chrome"]}'
    extra = os.getenv("YTDLP_EXTRA_OPTS", "").strip()
    if extra:
        try:
            opts.update(json.loads(extra))
        except json.JSONDecodeError:
            logger.warning("YTDLP_EXTRA_OPTS is not valid JSON — ignored")

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


class CookiesRequiredError(VideoDownloadError):
    """The platform authenticated the request and refused the session."""
    pass


class JSRuntimeMissingError(VideoDownloadError):
    """YouTube's signature / n-challenge needs a JS runtime that is not installed."""
    pass


class _YdlLogCapture:
    """Mirror yt-dlp output to our logger while remembering warnings/errors."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        text = str(msg).strip()
        # Suppress verbose channel + progress redraws, keep the extractor trace
        if not text or text.startswith("[debug]") or "[download]" in text:
            return
        logger.info(text)

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        text = str(msg).strip()
        self.messages.append(text)
        logger.warning(text)

    def error(self, msg: str) -> None:
        text = str(msg).strip()
        self.messages.append(text)
        logger.error(text)


def _attempt_download(
    video_url: str,
    platform: str,
    proxy: Optional[str],
    cookie: Optional[str],
) -> tuple[Optional[dict], Optional[str], list[str]]:
    """One yt-dlp run: returns (result, error, captured warnings)."""
    ydl_opts = build_ydl_opts(proxy, platform, cookie)
    ydl_opts["outtmpl"] = os.path.join(download_dir(), f"{uuid.uuid4()}.%(ext)s")
    capture = _YdlLogCapture()
    ydl_opts["logger"] = capture

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next((e for e in info["entries"] if e), None) or info
            filepath = ydl.prepare_filename(info)
    except yt_dlp.utils.DownloadError as exc:
        return None, str(exc), list(capture.messages)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected failure for %s", video_url)
        raise VideoDownloadError(str(exc)) from exc

    if not os.path.exists(filepath):
        return None, "Download completed but file missing on disk", list(capture.messages)

    ext = info.get("ext") or os.path.splitext(filepath)[1].lstrip(".")

    return {
        "filepath": filepath,
        "title": info.get("title"),
        "ext": ext,
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "webpage_url": info.get("webpage_url") or video_url,
    }, None, list(capture.messages)


def download_video(video_url: str) -> dict:
    """
    Download a video with yt-dlp and return its local path + metadata.

    Tries cookies+proxy first, then degrades (proxy-only / cookies-only / direct)
    because a stale cookie can be worse than no cookie at all. A proxy that answers
    407 is dropped for the rest of the run, and an anonymous bot check is retried
    with backoff because YouTube re-scores sessions.
    Caller is responsible for deleting the file after use.
    """
    platform = platform_of(video_url)
    _warn_if_no_js_runtime(platform)
    cookie = cookie_file_for(platform)
    proxy = proxy_for(platform)

    attempts: list[dict[str, Optional[str]]] = [{"proxy": proxy, "cookie": cookie}]
    if cookie:
        attempts.append({"proxy": proxy, "cookie": None})
    if proxy:
        attempts.append({"proxy": None, "cookie": cookie})
    if len(attempts) == 1:
        attempts.append({"proxy": None, "cookie": None})

    last_error: Optional[str] = None
    last_messages: list[str] = []
    proxy_usable = bool(proxy)

    for index, attempt in enumerate(attempts, start=1):
        if attempt["proxy"] and not proxy_usable:
            logger.warning(
                "Skipping attempt %s/%s: the proxy rejected the credentials",
                index,
                len(attempts),
            )
            continue

        logger.info(
            "Download attempt %s/%s url=%s platform=%s proxy=%s cookies=%s",
            index,
            len(attempts),
            video_url,
            platform or "-",
            bool(attempt["proxy"]),
            bool(attempt["cookie"]),
        )
        result, error, messages = _attempt_download(
            video_url, platform, attempt["proxy"], attempt["cookie"]
        )
        if result:
            return result

        last_error, last_messages = error, messages
        logger.warning(
            "Attempt %s failed: %s",
            index,
            (last_error or "").strip().splitlines()[-1][:300],
        )
        if attempt["proxy"] and _looks_like_proxy_auth_error(last_error or ""):
            proxy_usable = False
            logger.error(
                "Proxy rejected the credentials — check PROXY_USERNAME / "
                "PROXY_PASSWORD and PROXY_USER_FORMAT ('user-{username}' for "
                "Oxylabs datacenter, 'customer-{username}-country-{country}' for "
                "residential). Continuing without the proxy."
            )

    # No cookies configured and YouTube wants proof of humanity: wait and re-ask.
    if not cookie and _is_bot_check_error(last_error):
        for retry in range(1, BOT_CHECK_RETRIES + 1):
            delay = BOT_CHECK_DELAY_SECONDS * retry
            logger.warning(
                "Bot check hit; retrying anonymously in %.0fs (%s/%s)",
                delay,
                retry,
                BOT_CHECK_RETRIES,
            )
            time.sleep(delay)
            result, error, messages = _attempt_download(video_url, platform, None, None)
            if result:
                return result
            last_error, last_messages = error, messages
            logger.warning(
                "Bot-check retry %s failed: %s",
                retry,
                (last_error or "").strip().splitlines()[-1][:300],
            )

    combined = "\n".join([last_error or ""] + last_messages).lower()

    if any(hint in combined for hint in JS_RUNTIME_ERROR_HINTS):
        raise JSRuntimeMissingError(
            "YouTube's JS challenge could not be solved "
            f"(enabled={','.join(JS_RUNTIME_NAMES) or 'none'}; "
            f"detected: {_js_runtime_summary()}). Install Deno "
            "(https://deno.land — the Docker image already does) or Node >= 22, or "
            "point YTDLP_JS_RUNTIMES at a runtime you have. "
            f"Last error: {(last_error or '').strip().splitlines()[-1][:300]}"
        )

    if last_error and any(hint in last_error.lower() for hint in COOKIE_ERROR_HINTS):
        platform_label = (platform or "platform").upper()
        raise CookiesRequiredError(
            f"{platform or 'This platform'} requires a logged-in session. "
            f"Set COOKIES_{platform_label}_B64 to a Netscape cookies.txt export from a "
            f"logged-in account, or COOKIES_{platform_label}_FILE to a cookies.txt on "
            "disk. From a datacenter IP also add a PO token provider "
            "(bgutil-ytdlp-pot-provider). "
            f"Last error: {last_error.strip().splitlines()[-1][:300]}"
        )

    raise VideoDownloadError(last_error or "Download failed")

