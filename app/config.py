from pydantic import BaseModel
import os

class Settings(BaseModel):
    # Network
    ws_host: str = os.getenv("ECHOEAR_WS_HOST", "0.0.0.0")
    ws_port: int = int(os.getenv("ECHOEAR_WS_PORT", "9001"))

    # Device auth
    device_token_header: str = "x-device-token"

    # OpenAI API
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    openai_asr_model: str = os.getenv("OPENAI_ASR_MODEL", "whisper-1")
    openai_tts_model: str = os.getenv("OPENAI_TTS_MODEL", "tts-1")
    openai_tts_voice: str = os.getenv("OPENAI_TTS_VOICE", "alloy")

    # Audio parameters (PCM16)
    pcm_sample_rate: int = int(os.getenv("PCM_SAMPLE_RATE", "16000"))
    pcm_channels: int = int(os.getenv("PCM_CHANNELS", "1"))

settings = Settings()
