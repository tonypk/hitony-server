from pydantic import BaseModel
import os

class Settings(BaseModel):
    # Network
    ws_host: str = os.getenv("ECHOEAR_WS_HOST", "0.0.0.0")
    ws_port: int = int(os.getenv("ECHOEAR_WS_PORT", "9001"))

    # Device auth
    device_token_header: str = "x-device-token"

    # OpenClaw
    openclaw_url: str = os.getenv("OPENCLAW_URL", "http://localhost:18789/v1/responses")
    openclaw_token: str = os.getenv("OPENCLAW_TOKEN", "")
    openclaw_model: str = os.getenv("OPENCLAW_MODEL", "openclaw:main")

    # ASR/TTS endpoints
    asr_url: str = os.getenv("ASR_URL", "http://127.0.0.1:9101/transcribe")
    tts_url: str = os.getenv("TTS_URL", "http://127.0.0.1:9102/synthesize")

    # Audio parameters (PCM16)
    pcm_sample_rate: int = int(os.getenv("PCM_SAMPLE_RATE", "16000"))
    pcm_channels: int = int(os.getenv("PCM_CHANNELS", "1"))

settings = Settings()
