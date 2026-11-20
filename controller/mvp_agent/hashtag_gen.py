# app/services/hashtag_gen.py
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

gemini_client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY", ""),
    base_url="https://generativelanguage.googleapis.com/v1beta/",
    timeout=60.0,
    max_retries=1,
)

_TAG_RE = re.compile(r"[^\w]+", re.UNICODE)


def _clean_tag(token: str) -> str:
    tag = _TAG_RE.sub("", str(token or "").strip().lstrip("#"))
    return tag[:40]


def _trend_hint(trending) -> str:
    tags = []
    for tag in trending or []:
        cleaned = _clean_tag(tag)
        if cleaned:
            tags.append(cleaned)
    if not tags:
        return ""
    listed = ", ".join(f"#{tag}" for tag in tags[:10])
    return (
        f" These related tags are trending with this topic right now: {listed}. "
        f"Prefer them when they genuinely fit the video."
    )


def generate_hashtags(topic: str, tone: str, platform_cfg: dict) -> list[str]:
    prompt = (
        f"Suggest up to {platform_cfg['hashtags_max']} relevant hashtags "
        f"for a post titled: {topic}, tone: {tone}. "
        f"Mix niche and broad tags, keep every tag focused on this exact topic."
        f"{_trend_hint(platform_cfg.get('trending'))}"
    )
    resp = gemini_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "You generate effective social media hashtags."},
            {"role": "user", "content": prompt}
        ],
        n=1
    )

    content = resp.choices[0].message.content or ""
    tags: list[str] = []
    seen: set[str] = set()
    for token in content.split():
        tag = _clean_tag(token)
        if len(tag) < 2 or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append(tag)
        if len(tags) >= platform_cfg["hashtags_max"]:
            break
    return ["#" + tag for tag in tags]