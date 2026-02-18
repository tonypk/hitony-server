from pydantic import BaseModel
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Load .env file if it exists (before reading os.getenv)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Only set if not already in environment (env vars take precedence)
                if key not in os.environ:
                    os.environ[key] = value


def _sanitize_ascii(val: str) -> str:
    """Strip non-ASCII characters from config values (prevents encoding errors)"""
    return val.encode('ascii', errors='ignore').decode('ascii').strip()


class Settings(BaseModel):
    # Network
    ws_host: str = os.getenv("HITONY_WS_HOST", "0.0.0.0")
    ws_port: int = int(os.getenv("HITONY_WS_PORT", "9001"))

    # Device auth
    device_token_header: str = "x-device-token"

    # OpenAI API for ASR/TTS (sanitized to prevent 'ascii' codec errors)
    openai_api_key: str = _sanitize_ascii(os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = _sanitize_ascii(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_asr_model: str = _sanitize_ascii(os.getenv("OPENAI_ASR_MODEL", "whisper-1"))
    openai_tts_model: str = _sanitize_ascii(os.getenv("OPENAI_TTS_MODEL", "tts-1"))
    openai_tts_voice: str = _sanitize_ascii(os.getenv("OPENAI_TTS_VOICE", "alloy"))

    # Intent / Chat model (OpenAI GPT for understanding user intent)
    intent_model: str = _sanitize_ascii(os.getenv("INTENT_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")))
    openai_chat_model: str = _sanitize_ascii(os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"))  # Used by admin panel

    # Music
    music_max_duration_s: int = int(os.getenv("MUSIC_MAX_DURATION", "600"))

    # Auth / DB
    secret_key: str = os.getenv("SECRET_KEY", "change-me-in-production-please")

    # Meeting cleanup
    meeting_retention_days: int = int(os.getenv("MEETING_RETENTION_DAYS", "7"))

    # Audio parameters (PCM16)
    pcm_sample_rate: int = int(os.getenv("PCM_SAMPLE_RATE", "16000"))
    pcm_channels: int = int(os.getenv("PCM_CHANNELS", "1"))
    frame_duration_ms: int = int(os.getenv("FRAME_DURATION_MS", "60"))

settings = Settings()

# Validate SECRET_KEY is not the weak default — refuse to start with insecure key
if settings.secret_key == "change-me-in-production-please" or len(settings.secret_key) < 16:
    raise SystemExit(
        "FATAL: SECRET_KEY is weak or default! "
        "Set a strong SECRET_KEY (>=16 chars) in .env before starting the server."
    )

# Log config for debugging
_oai_key = '***' + settings.openai_api_key[-4:] if len(settings.openai_api_key) > 4 else 'EMPTY'
logger.info(f"Config: ASR/TTS → {settings.openai_base_url} (key={_oai_key})")
logger.info(f"Config: Intent → {settings.openai_base_url}, model={settings.intent_model}")
