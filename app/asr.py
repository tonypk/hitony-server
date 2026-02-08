import httpx
from .config import settings

async def transcribe_pcm(pcm_bytes: bytes) -> str:
    # Assumes ASR service accepts raw PCM via multipart/form-data
    async with httpx.AsyncClient(timeout=120) as client:
        files = {"file": ("audio.pcm", pcm_bytes, "application/octet-stream")}
        data = {"rate": str(settings.pcm_sample_rate), "channels": str(settings.pcm_channels)}
        resp = await client.post(settings.asr_url, files=files, data=data)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("text", "")
