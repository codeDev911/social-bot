# app/services/caption_gen.py
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

gemini_client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY", ""),
    base_url="https://generativelanguage.googleapis.com/v1beta/",
    # A stuck model call would hold a queue slot open forever.
    timeout=60.0,
    max_retries=1,
)

_BULLET_RE = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s*")


def _clean_option(line: str) -> str:
    return _BULLET_RE.sub("", (line or "").strip()).strip().strip('"')


# Generate for Starter
def generate_captions(topic: str, tone: str, platform_cfg: dict) -> list[str]:
    prompt = (
        f"You are a clever social media assistant. Generate 3 caption options "
        f"(max {platform_cfg['caption_max']} chars) for a {tone} post about: {topic}. "
        f"Put each option on its own line, no numbering, no quotes."
    )
    resp = gemini_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "You generate catchy social media captions."},
            {"role": "user", "content": prompt}
        ],
        n=1
    )
    content = resp.choices[0].message.content or ""
    options = [_clean_option(line) for line in content.splitlines()]
    return [option for option in options if option][:3]

# Generate for Pro