# app/services/hashtag_gen.py
from openai import OpenAI

gemini_client = OpenAI(
    api_key="AIzaSyAs8ijk-UEW3F9TcO1A4uBRbtgL44Dxr80",
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
