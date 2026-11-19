# app/services/hashtag_gen.py
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

gemini_client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY", ""),
    base_url="https://generativelanguage.googleapis.com/v1beta/"
)

def generate_hashtags(topic: str, tone: str,  platform_cfg: dict) -> list[str]:
    prompt = (
        f"Suggest up to {platform_cfg['hashtags_max']} relevant hashtags "
        f"for a post titled: {topic},  tone: {tone}. Use both niche and broad tags."
    )
    resp = gemini_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "You generate effective social media hashtags."},
            {"role": "user", "content": prompt}
        ],
        n=1
    )
    tags = [t.strip().lstrip("#") for t in resp.choices[0].message.content.split() if t.strip()]
    return ["#" + t for t in tags][:platform_cfg["hashtags_max"]]
