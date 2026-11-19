# social-bot
# v20
# hi dear
## issuing with Fly billing

# Services we use
- yt-dlp
- cron-job.org -> for scheduling jobs
- gmail smtp for mail
- Webshare -> proxy for yt-dlp

# Downloads and platform blocking

YouTube, Instagram and Facebook authenticate the *session*, not the IP, so a proxy
alone cannot fix "pass cookies.txt". `controller/video_downloader.py` now:

- loads a Netscape `cookies.txt` per platform from `COOKIES_<PLATFORM>_B64`
  (`base64 -w0 cookies.txt`), written to `COOKIES_DIR` at runtime;
- points yt-dlp at ffmpeg (`imageio-ffmpeg`) so separate video/audio streams mux;
- keeps `PROXY_DISABLE_PLATFORMS` (typical value: `tiktok`) off the proxy;
- retries cookies+proxy -> proxy-only -> cookies-only -> direct, and raises
  `CookiesRequiredError` naming the exact env var to set;
- retries an anonymous `Sign in to confirm you're not a bot` with backoff
  (`BOT_CHECK_RETRIES`, `BOT_CHECK_DELAY`), because that verdict is scored per
  session and frequently clears on a fresh one.

YouTube also needs an external JS runtime for its JS challenges. yt-dlp only
enables Deno by default, so `YTDLP_JS_RUNTIMES` (default `deno,node,bun`) lets
Node >= 22 or Bun stand in; anything not installed is skipped. The Docker image
installs Deno, `yt-dlp-ejs` is pinned in `requirements.txt`, and the worker's
`GET /health` reports which runtimes were detected.

Datacenter proxies (`dc.oxylabs.io`, `93.115.200.x`) are classified like cloud
IPs. Use residential (`PROXY_ENTRYPOINTS=pr.oxylabs.io:7777:US`) for
YouTube/Instagram, or run those platforms without a proxy. Oxylabs authenticates
as `user-<username>` on datacenter and `customer-<username>-country-<cc>` on
residential — override with `PROXY_USER_FORMAT`. A proxy that answers `407` is
dropped for the rest of that download instead of being retried, and the log says
so.

## Getting a cookies.txt

1. Log in to the platform in a browser profile you can afford to lose (a burner
   account is safer than the account you publish from).
2. Export cookies with a "Get cookies.txt LOCALLY" style extension on the
   domain, e.g. `youtube.com`.
3. Base64 it and paste the value into the deploy env:

    # Linux / macOS
    base64 -w0 cookies.txt

    # Windows PowerShell
    [Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt"))

4. Set `COOKIES_YOUTUBE_B64` (or the matching platform variable) and redeploy.
   Locally you can skip base64 entirely: drop the file somewhere and set
   `COOKIES_YOUTUBE_FILE=C:\path\to\cookies.txt` (takes precedence).

Cookies expire — when a platform starts failing again, re-export. `GET /health`
on the worker tells you whether a cookie file is actually loaded.

If YouTube keeps answering the bot check even with fresh cookies *and* you are on
a datacenter IP, the missing piece is a PO token: add
`bgutil-ytdlp-pot-provider` (yt-dlp plugin + its HTTP provider container). Cookies
authenticate the account; the PO token proves the player was real.

# Scaling downloads horizontally (worker service)

`worker_service.py` runs yt-dlp + Cloudinary in its own container and reports
back through a signed webhook, so the web host no longer needs Deno, ffmpeg or a
long request timeout.

    uvicorn worker_service:app --host 0.0.0.0 --port 8080

Flow: web app `POST /jobs` (`X-Worker-Token`) -> worker downloads and uploads to
Cloudinary -> `POST /dashboard/api/worker/ingest` signed with
`X-Worker-Signature: sha256=HMAC_SHA256(INTERNAL_SECRET, raw_body)` ->
`create_video()` stores it as Pending.

Set `WORKER_SERVICE_URL` and `WORKER_TOKEN` to route downloads there. On Vercel
set both — with the app as a serverless function there is nowhere else for a
download to run. Leave both empty only where the app runs as a normal long-lived
server. `GET /health` on the worker reports whether Deno, ffmpeg and each
platform's cookies are actually present, so check it before blaming the platform.

# Queue visibility

Submitting a URL now writes a Pending row immediately, before the download starts,
with `job:<worker job id>` in `video.upload_id`. That row is what the dashboard
shows, and `GET /dashboard/api/queue` merges it with the worker's live phase
(queued / downloading / uploading). The tables poll it every 4 seconds while a
job is in flight and render a badge: QUEUED, DOWNLOADING, UPLOADING, PENDING when
ready, or FAILED with the reason as a tooltip.

One job maps to exactly one row: the worker's webhook carries `jobId`, fills that
placeholder in place instead of inserting a second video, and the plan slot is
counted once at queue time. Failures are reported on the same webhook, so a
broken job turns red instead of sitting in the queue forever. Placeholders never
reach the publisher — `/dashboard/trigger-upload` skips rows that have no
Cloudinary URL yet.

# Deploying: Vercel for the app, one box for the worker

The web app runs on Vercel. Everything that needs a process — yt-dlp, Cloudinary
upload, retries — runs on a separate always-on host.

## App (Vercel)

Set on the Vercel project:

    WORKER_SERVICE_URL = https://<worker-host>     # must be https: httpx verifies TLS
    WORKER_TOKEN       = <same value as on the worker>
    INTERNAL_SECRET    = <shared with the worker; signs the ingest webhook>

Why the split exists, in Vercel's own terms:

- `vercel.json` pins `maxDuration: 60`. A YouTube download plus Cloudinary upload
  does not reliably fit, and a function killed at the cap returns no error to the
  caller. Background tasks execute inside the same invocation, so they are bounded
  by that budget — they do not outlive it.
- No long-lived process, so nothing there can poll, retry or resume a job.
- Egress is AWS, so the IP-reputation problem is the same as on any cloud host.

`/dashboard/trigger-upload` (cron-job.org) stays on Vercel: with a worker
configured it only fans jobs out and returns in about a second.

If Vercel **Deployment Protection** is enabled, the worker's callback is rejected:
allow `/dashboard/api/worker/ingest` through, or send a protection-bypass token
from the worker.

## Worker (Render / Railway / any VPS)

Start command, when the platform builds the `Dockerfile` directly (Render: the
"Docker Command" field; Railway: "Start Command"):

    uvicorn worker_service:app --host 0.0.0.0 --port $PORT

or on a VPS, `docker-compose.worker.yml` does the same:

    docker compose -f docker-compose.worker.yml up -d --build

Set on the worker host:

    WORKER_TOKEN, INTERNAL_SECRET
    APP_API_URL = https://<your-app>.vercel.app
    CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET
    COOKIES_YOUTUBE_B64 (burner account), PROXY_* if you have working credentials

This host holds the cookies, the JS runtime (Deno is in the image) and the uptime.
It never touches the database — results go back through the signed webhook.
`GET /health` reports the runtimes, ffmpeg, which cookie files loaded and the
callback URL; check it before blaming a platform.

## Other shapes

- Fly.io — `fly.toml` remains as an example of running both process groups from
  one image. Ignore or delete it if you never use Fly.
- Single VPS for everything — `docker compose up -d --build` (app + worker).

Every variable the app understands is declared in `.env.example`; run
`python scripts/env_keys.py --audit` after adding one and
`python scripts/env_keys.py` to catch typos in your local `.env`.

Cloudflare Workers cannot host this: 128 MB per isolate, CPU caps, no Python
binaries, no filesystem. Use a container host (Render, Railway, Fly.io,
Cloudflare Containers) and run N replicas.

Downloading and re-publishing platform content is subject to each platform's terms
and to copyright law; that compliance call sits with the product, not the
downloader.