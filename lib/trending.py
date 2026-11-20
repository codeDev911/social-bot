"""Trending hashtag discovery for the caption worker.

Two independent, key-free signals are merged:

1. YouTube / Google autocomplete for the video title — real search demand.
2. DuckDuckGo result titles and snippets that mention explicit ``#hashtags``.

Results are cached in Redis for ``TRENDING_CACHE_TTL`` seconds (12h by
default). Every failure path returns an empty list so hashtag generation still
works from the model alone; nothing here is allowed to fail a job.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Iterable, Optional

import httpx

logger = logging.getLogger(__name__)

CACHE_PREFIX = "socialbot:ai:trending:"
CACHE_TTL = int(os.getenv("TRENDING_CACHE_TTL") or 12 * 3600)
MAX_TERM = 32
DISABLED = (os.getenv("TRENDING_DISABLED") or "").strip().lower() in {"1", "true", "yes", "on"}

HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,40})")
WORD_RE = re.compile(r"[A-Za-z0-9]+")

_sync_redis = None


def trending_terms(topic: str, platform: str = "", limit: int = 10) -> list[str]:
    """Return up to ``limit`` tag candidates (without the leading '#')."""
    if DISABLED:
        return []
    topic = (topic or "").strip()
    if len(topic) < 3:
        return []

    cache_key = CACHE_PREFIX + hashlib.sha1(
        f"{topic.lower()}|{platform.lower()}".encode()
    ).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[:limit]

    started = time.time()
    terms: list[str] = []
    terms.extend(_autocomplete(topic, platform))
    terms.extend(_web_hashtags(topic, platform))

    cleaned = _dedupe(terms)
    _cache_set(cache_key, cleaned)
    logger.info(
        "Trending: %d candidates for %r in %.1fs",
        len(cleaned),
        topic[:60],
        time.time() - started,
    )
    return cleaned[:limit]


def _dedupe(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        tag = _clean_term(term)
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
    return out


def _clean_term(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "", str(value or "")).strip("_")
    return value[:MAX_TERM] if len(value) >= 3 else ""


def _to_camel_tag(text: str) -> str:
    words = WORD_RE.findall(str(text or ""))[:4]
    if not words:
        return ""
    tag = "".join(word.capitalize() for word in words)
    return tag[:MAX_TERM] if len(tag) >= 4 else ""


def _autocomplete(topic: str, platform: str) -> list[str]:
    """Search-suggestion demand is the cheapest real trend signal available."""
    queries = [topic]
    if platform:
        queries.append(f"{topic} {platform}")

    out: list[str] = []
    try:
        with httpx.Client(
            timeout=6.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; social-bot/1.0)"},
        ) as client:
            for query in queries[:2]:
                try:
                    response = client.get(
                        "https://suggestqueries.google.com/complete/search",
                        params={"client": "firefox", "hl": "en", "ds": "yt", "q": query},
                    )
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Autocomplete failed for %r: %s", query[:40], exc)
                    continue
                suggestions = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
                for suggestion in suggestions[:8]:
                    tag = _to_camel_tag(suggestion)
                    if tag:
                        out.append(tag)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Autocomplete client failed: %s", exc)
    return out


def _web_hashtags(topic: str, platform: str) -> list[str]:
    """Explicit hashtags mentioned in fresh DuckDuckGo results, ranked by use."""
    try:
        from ddgs import DDGS
    except Exception:  # pragma: no cover - ddgs is pinned in requirements.txt
        return []

    query = f'"{topic}" trending hashtags'
    if platform:
        query = f"{query} {platform}"

    counts: dict[str, int] = {}
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=6))
    except Exception as exc:  # noqa: BLE001
        logger.debug("DDG trending search failed: %s", exc)
        return []

    for result in results or []:
        text = f"{result.get('title', '')} {result.get('body', '')}"
        for match in HASHTAG_RE.findall(text):
            tag = _clean_term(match)
            if tag:
                counts[tag] = counts.get(tag, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    return [tag for tag, _ in ranked[:12]]


def _cache_get(key: str) -> Optional[list[str]]:
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except Exception:  # noqa: BLE001
        return None


def _cache_set(key: str, terms: list[str]) -> None:
    client = _redis()
    if client is None or not terms:
        return
    try:
        client.set(key, json.dumps(terms), ex=CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass


def _redis():
    """Sync client: this module runs inside worker threads, not the event loop."""
    global _sync_redis
    url = (os.getenv("REDIS_URL") or "").strip().strip("'\"").strip()
    if not url:
        return None
    if _sync_redis is None:
        try:
            import redis

            _sync_redis = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Trending cache disabled: %s", exc)
            return None
    return _sync_redis