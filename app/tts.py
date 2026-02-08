import httpx
from .config import settings

async def synthesize_tts(text: str) -> bytes:
    async with httpx.AsyncClient(timeout=120) as client:
        payload = {
            "text": text,
            "rate": settings.pcm_sample_rate,
            "channels": settings.pcm_channels,
            "format": "pcm16"
        }
        resp = await client.post(settings.tts_url, json=payload)
        resp.raise_for_status()
        return resp.content
