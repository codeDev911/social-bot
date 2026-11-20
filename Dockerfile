# Use a slim version of Python as the base image
FROM python:3.11-slim as app_builder

 
# Set the working directory inside the container
WORKDIR /app

ENV PYTHONUNBUFFERED=1

# ── System dependencies ───────────────────────────────────────────────────────
# ffmpeg is provided by imageio-ffmpeg (see controller/video_downloader.ffmpeg_path);
# curl + unzip are only needed to install Deno below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Deno ──────────────────────────────────────────────────────────────────────
# yt-dlp needs an external JS runtime to solve YouTube's JS challenges (EJS).
# Without it every YouTube download fails even with valid cookies.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y \
    && ln -sf /usr/local/bin/deno /usr/bin/deno \
    && deno --version

# Copy the Python requirements file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt 

# The Prisma python client is generated per environment; a fresh image has none.
# The dummy URL only satisfies the schema's env() reference — generate needs no DB.
COPY prisma /app/prisma
RUN DATABASE_URL="postgresql://user:pass@localhost:5432/db" prisma generate


# Copy the app files to the container
COPY . /app/

# Web app entrypoint. Deploy this image three ways, all from the same build:
#   web app:            CMD ["sh", "-c", "prisma db push --accept-data-loss && uvicorn main:app --host 0.0.0.0 --port 8080"]
#   download worker:    CMD ["uvicorn", "worker_service:app", "--host", "0.0.0.0", "--port", "8080"]
#   caption worker:     CMD ["python", "wroker.py"]   (reads REDIS_URL, needs one replica)
CMD ["sh", "-c", "prisma db push --accept-data-loss && uvicorn main:app --host 0.0.0.0 --port 8080"]
