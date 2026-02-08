import httpx
from .config import settings

async def call_openclaw(text: str) -> str:
    headers = {"Content-Type": "application/json"}
    if settings.openclaw_token:
        headers["Authorization"] = f"Bearer {settings.openclaw_token}"

    payload = {
        "model": settings.openclaw_model,
        "input": text,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(settings.openclaw_url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["output"][0]["content"][0]["text"]
        except Exception:
            return str(data)
