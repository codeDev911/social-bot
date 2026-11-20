"""Caption + hashtag worker.

Every video row the app creates hands one job to Redis. This process consumes
those jobs, generates a caption from the title plus trending hashtags, writes
both to Postgres and keeps a live status in Redis that the dashboard reads.

Run it next to the web app (same image, different command):

    python wroker.py                     # long-running consumer
    python wroker.py --once              # drain due work, then exit
    python wroker.py --backfill          # queue existing Pending videos
    AI_WORKER_CONCURRENCY=2 python wroker.py

Environment:
    DATABASE_URL      Postgres (same value as the web app)
    REDIS_URL         broker + status hashes (required)
    GEMINI_API_KEY    used by the caption / hashtag generators

Run one replica per queue: in-flight jobs are recovered on startup, and two
workers recovering at the same time would double-process them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

from db import prisma
from lib import ai_queue, redis_client
from lib.ai_pipeline import PermanentJobError, run_ai_job

logger = logging.getLogger("ai-worker")

MAX_ATTEMPTS = int(os.getenv("AI_JOB_MAX_ATTEMPTS") or 3)
POLL_TIMEOUT = int(os.getenv("AI_WORKER_POLL_TIMEOUT") or 5)
RETRY_DELAY = int(os.getenv("AI_JOB_RETRY_DELAY") or 45)
CONCURRENCY = max(1, int(os.getenv("AI_WORKER_CONCURRENCY") or 2))


def _config(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Caption and hashtag queue worker")
    parser.add_argument("--once", action="store_true", help="drain due jobs and exit")
    parser.add_argument("--backfill", action="store_true", help="queue existing Pending videos and exit")
    parser.add_argument("--limit", type=int, default=25, help="videos to inspect during --backfill")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


async def _process(raw: str, client) -> None:
    """Run one message; ack, retry or dead-letter it."""
    try:
        job = json.loads(raw)
        video_id = int(job["videoId"])
        user_id = int(job["userId"])
    except Exception as exc:  # noqa: BLE001 - a poisoned message must not wedge the queue
        logger.error("Dropping malformed job (%s): %s", exc, raw[:200])
        await client.lrem(ai_queue.PROCESSING_KEY, 1, raw)
        await client.lpush(ai_queue.DEAD_KEY, raw)
        await client.ltrim(ai_queue.DEAD_KEY, 0, ai_queue.DEAD_MAX - 1)
        return

    attempts = int(job.get("attempts") or 0) + 1
    job["attempts"] = attempts
    job.setdefault("startedAt", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    await ai_queue.set_status(
        video_id,
        status="processing",
        phase="starting",
        attempts=attempts,
        error="",
        startedAt=job["startedAt"],
    )

    try:
        await run_ai_job(job)
        await client.lrem(ai_queue.PROCESSING_KEY, 1, raw)
    except PermanentJobError as exc:
        logger.error("Video %s can no longer be processed: %s", video_id, exc)
        await client.lrem(ai_queue.PROCESSING_KEY, 1, raw)
        await ai_queue.set_status(
            video_id, status="failed", phase="failed", attempts=attempts, error=str(exc)[:300]
        )
        await ai_queue.release(video_id, user_id)
    except Exception as exc:  # noqa: BLE001
        await client.lrem(ai_queue.PROCESSING_KEY, 1, raw)
        if attempts < MAX_ATTEMPTS:
            delay = RETRY_DELAY * attempts
            await ai_queue.schedule_retry(client, job, delay)
            await ai_queue.set_status(
                video_id,
                status="queued",
                phase="retry",
                attempts=attempts,
                error=str(exc)[:300],
                nextAttemptIn=delay,
            )
            logger.warning(
                "Video %s failed (attempt %s/%s), retrying in %ss: %s",
                video_id,
                attempts,
                MAX_ATTEMPTS,
                delay,
                exc,
            )
        else:
            await ai_queue.dead_letter(client, job)
            await ai_queue.set_status(
                video_id, status="failed", phase="failed", attempts=attempts, error=str(exc)[:300]
            )
            await ai_queue.release(video_id, user_id)
            logger.error("Video %s failed permanently after %s attempts: %s", video_id, attempts, exc)


async def _loop(index: int, stop: asyncio.Event) -> None:
    client = await redis_client.get_client()
    if client is None:
        logger.error("Worker %s has no Redis client", index)
        return

    logger.info("Worker %s ready (queue %s)", index, ai_queue.QUEUE_KEY)
    while not stop.is_set():
        try:
            await ai_queue.heartbeat(client)
            await ai_queue.promote_retries(client)
            raw = await client.brpoplpush(
                ai_queue.QUEUE_KEY, ai_queue.PROCESSING_KEY, timeout=POLL_TIMEOUT
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - Redis blips must not kill the worker
            logger.warning("Worker %s poll failed: %s", index, exc)
            await asyncio.sleep(2)
            continue

        if raw:
            await _process(raw, client)


async def _drain(client) -> int:
    """--once: process everything currently due, then return."""
    processed = 0
    while True:
        await ai_queue.promote_retries(client)
        raw = await client.brpoplpush(
            ai_queue.QUEUE_KEY, ai_queue.PROCESSING_KEY, timeout=1
        )
        if not raw:
            return processed
        await _process(raw, client)
        processed += 1


def _install_signals(stop: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    def request_stop(*_args) -> None:
        logger.info("Shutdown requested - finishing the current job")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError, AttributeError):
            # Windows: fall back to the plain signal handler.
            try:
                signal.signal(sig, request_stop)
            except (ValueError, OSError):
                pass


async def _main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not redis_client.redis_enabled():
        logger.error("REDIS_URL is not set - the AI worker needs Redis to receive jobs")
        return 2

    await prisma.connect()
    client = await redis_client.get_client()
    if client is None:
        await prisma.disconnect()
        return 2

    try:
        if args.backfill:
            queued = await ai_queue.backfill_candidates(limit=max(1, args.limit))
            logger.info("Backfill queued %s video(s)", queued)
            return 0

        recovered = await ai_queue.recover_inflight(client)
        if recovered:
            logger.info("Recovered %s in-flight job(s) from the previous run", recovered)

        if args.once:
            processed = await _drain(client)
            logger.info("Drained %s job(s)", processed)
            return 0

        stop = asyncio.Event()
        _install_signals(stop, asyncio.get_running_loop())

        workers = [asyncio.create_task(_loop(index + 1, stop)) for index in range(CONCURRENCY)]
        logger.info("Caption worker started (%s loop(s), max attempts %s)", len(workers), MAX_ATTEMPTS)
        await stop.wait()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        logger.info("Caption worker stopped")
        return 0
    finally:
        await redis_client.close_client()
        await prisma.disconnect()


def main(argv: Optional[list[str]] = None) -> int:
    args = _config(argv)
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())