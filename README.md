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
a datacenter IP, the missing piece is a PO token. Cookies authenticate the
account; the PO token proves the player was real. On a datacenter IP you need
both — neither one alone is reliable any more.

## PO token provider (bgutil)

`Dockerfile.worker` installs the `bgutil-ytdlp-pot-provider` yt-dlp plugin and
builds its provider server (Deno, no extra Node install). `worker_service.py`
starts it on `127.0.0.1:4416` at boot — the plugin's default address — and stops
it on shutdown. Nothing on the app side changes; jobs keep flowing.

Verify after a deploy:

1. `GET /health` → `"potProvider": {"enabled": true, "running": true, "port": 4416}`.
   If `running` is false, the image build could not prepare the provider — check
   the build log for `bgutil provider build failed`.
2. Set `YTDLP_VERBOSE=1` on the worker, run one YouTube job, and look for
   `PO Token Providers: bgutil:http-...` and
   `[youtube] [pot:bgutil:http] Generating a gvs PO Token ...` in the worker log.
   If the provider is listed but no "Generating" line appears, the player clients
   failed earlier (usually still cookies); set `COOKIES_YOUTUBE_B64`.

`BGUTIL_PROVIDER_DISABLE=true` turns it off; `BGUTIL_PROVIDER_PORT` moves it.
The provider is unauthenticated and listens on loopback only — never publish
port 4416 on the host.

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

# Caption + hashtag worker (Redis)

Every created video is queued for AI copy the moment its row exists, from
`create_video()` / `complete_queued_video()`, so URL downloads, direct uploads,
Pexels clips, the clipper and worker ingests all get covered without touching a
router. `wroker.py` consumes the queue and writes one caption (from the video
title) plus the most-talked-about hashtags for the video's platform back to
`video.captions` and `video.htags`.

    python wroker.py              # long-running consumer (one replica per queue)
    python wroker.py --once       # drain everything currently queued, then exit
    python wroker.py --backfill   # also queue existing videos that have no copy

Set `REDIS_URL` (e.g. `rediss://default:password@host:6379/0`). Without it the
queue reports "disabled" and generation runs inline right after the upload, so
local development needs no Redis. **The worker cannot run on Vercel** — deploy
it as its own Docker service from the same image (`CMD ["python", "wroker.py"]`).

Keys live under `socialbot:ai:`: `queue` (pending list), `queue:processing`
(in-flight, recovered on startup), `queue:retry` (backoff ZSET), `queue:dead`
(capped at 100), `queue:heartbeat` (90s TTL) and `queue:status:<videoId>`
(7-day hash the dashboard reads). A per-video lock (`queue:lock:<id>`) stops a
video being queued twice; a job retries 3 times before landing in the dead list.

Trending tags are discovered without API keys: YouTube/Google autocomplete for
the video title plus explicit `#hashtags` in fresh DuckDuckGo results, cached for
12 hours. Every discovery failure returns an empty list, so hashtags still come
from the model alone.

Environment: `REDIS_URL`, `AI_QUEUE_DISABLED`, `AI_QUEUE_KEY`,
`AI_WORKER_CONCURRENCY`, `AI_JOB_MAX_ATTEMPTS`, `AI_JOB_RETRY_DELAY`,
`AI_STATUS_TTL`, `AI_JOB_LOCK_TTL`, `AI_DEFAULT_TONE`, `TRENDING_CACHE_TTL`,
`TRENDING_DISABLED`.

The dashboard home cards are live: **Next Scheduled** shows the next run of the
user's daily cron in their timezone plus caption/hashtag state for the next
video, and **Processing Queue** lists download placeholders, running AI jobs and
videos waiting for the publish run. Both refresh through
`GET /dashboard/api/queue?view=1` every 5 seconds. The regenerate button on that
card posts to `POST /dashboard/api/videos/{id}/generate`.

# Playground: default transformations for every upload

`/dashboard/playground` (sidebar: **Playground**) is not a per-video tool — it
holds the **default anti-copyright settings** applied to every video on its way
to Cloudinary. URL downloads, worker jobs and direct file uploads all read the
same chain, in this order:

    audio -> trim -> crop -> grain (vignette/noise) -> text overlay -> quality

The page explains every technique (visual fingerprint, audio fingerprint, intro
trim, watermark, re-encode), shows a **protection meter**, and renders a live
Cloudinary preview over the newest video in the library. Saving writes a single
JSON config:

- `DynamicEdit.playgroundTransformations` — authoritative; read by
  `user_transformations()` in `controller/url_video_controller.py`.
- The legacy `audioControls` / `textOverlayControls` /
  `pixelDistortionControls` columns are mirrored so the old
  "Anti-Copyright Settings" modal in `/dashboard/settings` stays accurate — and
  a save there folds back into `playgroundTransformations`, so the two editors
  never fight.

Accounts that never open the Playground keep the legacy anti-copyright
behaviour; turning the master toggle off uploads videos untouched. Existing
videos are never modified.

# Smart Clipper: clips without uploading the source

`/dashboard/clipper` (sidebar: **Smart Clipper**) turns a local video into 8–13
vertical-ready clips. The source file is only ever read in the browser via
`URL.createObjectURL` — a 30-minute source is never sent to Cloudinary or to the
server, which matters on Vercel (4.5 MB body cap, `maxDuration: 60`).

How it works:

1. The browser samples 24–180 frames (one 160x90 thumbnail every few seconds),
   scores activity as frame difference and picks the busiest non-overlapping
   windows. Clip length and clip count start from `clamp(5s, 45s, duration/12)`
   and `~8–13 clips`, but both are editable in **Clip settings** — changing them
   re-plans instantly from the last scan (no re-scanning).
2. Every detected window shows editable **from / to** seconds (bounded by the
   plan's per-clip cap), a per-clip preview button, and an **Add clip at
   playhead** action for manual picks.
3. **Aspect ratio** (keep source, 9:16, 4:5, 1:1, 16:9) with *crop to fill* or
   *fit with black bars* re-frames each clip onto a 720p-class canvas while it is
   recorded; "keep source" records the element stream directly.
4. Each selected window is re-recorded with `captureStream()` + `MediaRecorder`
   (vp9/vp8 + opus, ~2.5 Mbps, canvas video track + source audio tracks when
   re-framing).
5. The blob goes **directly to Cloudinary** from the browser using a signature
   from `POST /api/clipper/signature` (server never touches the media).
6. `POST /api/clipper/complete` verifies the asset exists, enforces the
   per-clip duration cap and the plan's queue slots, then writes the Pending row
   with its `scheduled_at`.

The publish section takes a title prefix, targets, a start time and a spacing
(0 / 15 / 30 / 60 / 1440 minutes) so clips drip out one by one.

Plan matrix (`lib/plans.py`) — the clipper is open to **all plans** for now; the
scheduled-video quota is what separates them:

| plan    | playground defaults | smart clipper | clips / run | source max | clip max |
|---------|---------------------|---------------|-------------|------------|----------|
| free    | yes                 | yes           | 13          | 30 min     | 45 s     |
| starter | yes                 | yes           | 13          | 30 min     | 45 s     |
| pro     | yes                 | yes           | 13          | 30 min     | 45 s     |

The publisher only picks rows whose `scheduled_at` has arrived
(`{"scheduled_at": {"lte": now}}`), so future clips wait in the queue instead of
publishing early.

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

### "Vercel logs still show a download"

The app only dispatches when **both** `WORKER_SERVICE_URL` and `WORKER_TOKEN` are
readable at runtime. In order of likelihood:

1. The variables were added to Preview/Development but not **Production**
   (Vercel → Settings → Environment Variables shows the scope of each row).
2. They were added after the last build — env changes only reach a new
   deployment, so **Redeploy**.
3. A pasted value carries a trailing newline, quotes or the variable name itself
   (`WORKER_SERVICE_URL=https://...`). Trailing whitespace and quotes are trimmed
   now; a doubled name is not. The boot log prints
   `Download worker configured: ...` or `No download worker configured ...`.
4. The token on Vercel differs from the worker's — the worker answers 401 and the
   dispatch is recorded as failed.
5. The worker host is asleep or crashed (Render Free spins down); dispatch waits
   `WORKER_DISPATCH_TIMEOUT` seconds, then reports unreachable.

Check it without guessing: open the dashboard — the line under **Video URL**
reads `Worker online`, `Worker configured but unreachable — ...` or
`No worker configured`. The same JSON is at `GET /dashboard/api/worker/diagnostics`.

A Vercel deployment can no longer quietly fall back to a local download: when the
worker is unreachable the submit endpoint returns `502` with the reason, and
`download_video()` itself refuses to run on Vercel (see `ALLOW_VERCEL_DOWNLOAD`).

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