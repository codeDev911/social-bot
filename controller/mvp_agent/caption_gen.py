# app/services/caption_gen.py
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

gemini_client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY", ""),
    base_url="https://generativelanguage.googleapis.com/v1beta/"
)

# Generate for Starter
def generate_captions(topic: str, tone: str, platform_cfg: dict) -> list[str]:
    prompt = (
        f"You are a clever social media assistant. Generate 3 caption options "
        f"(max {platform_cfg['caption_max']} chars) for a {tone} post about: {topic}."
    )
    resp = gemini_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "You generate catchy social media captions."},
            {"role": "user", "content": prompt}
        ],
        n=1
    )
    content = resp.choices[0].message.content
    return [c.strip() for c in content.split("\n") if c.strip()]

# Generate for Pro