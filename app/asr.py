import io
import struct
import logging
from typing import Optional
from openai import AsyncOpenAI
from .config import settings
from .session import Session

logger = logging.getLogger(__name__)

# Global default client
_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)


def _get_client(session: Optional[Session] = None) -> AsyncOpenAI:
    """Return per-user client if session has custom API key, else global."""
    if session and session.config.openai_api_key:
        return AsyncOpenAI(
            api_key=session.config.openai_api_key,
            base_url=session.config.get("openai_base_url", settings.openai_base_url),
        )
    return _client

def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Convert raw PCM16 mono 16kHz to WAV format in memory"""
    num_channels = settings.pcm_channels
    sample_rate = settings.pcm_sample_rate
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_bytes)

    buf = io.BytesIO()
    # RIFF header
    buf.write(b'RIFF')
    buf.write(struct.pack('<I', 36 + data_size))
    buf.write(b'WAVE')
    # fmt chunk
    buf.write(b'fmt ')
    buf.write(struct.pack('<I', 16))  # chunk size
    buf.write(struct.pack('<H', 1))   # PCM format
    buf.write(struct.pack('<H', num_channels))
    buf.write(struct.pack('<I', sample_rate))
    buf.write(struct.pack('<I', byte_rate))
    buf.write(struct.pack('<H', block_align))
    buf.write(struct.pack('<H', bits_per_sample))
    # data chunk
    buf.write(b'data')
    buf.write(struct.pack('<I', data_size))
    buf.write(pcm_bytes)
    return buf.getvalue()


# Whisper hallucination patterns — produced from silence/noise input
_HALLUCINATIONS = {
    "thank you", "thank you for watching", "thanks for watching",
    "thanks", "bye", "goodbye", "all right", "you", "the end",
    "subscribe", "like and subscribe", "see you next time",
    "so", "okay", "yeah", "yes", "no", "hmm", "uh",
}


async def transcribe_pcm(pcm_bytes: bytes, session: Optional[Session] = None) -> str:
    """Transcribe PCM audio using OpenAI Whisper API"""
    wav_bytes = _pcm_to_wav(pcm_bytes)
    duration_s = len(pcm_bytes) / 2 / settings.pcm_sample_rate
    logger.info(f"ASR: sending {len(pcm_bytes)} bytes PCM ({duration_s:.1f}s, {len(wav_bytes)} bytes WAV) to Whisper")

    # Filter very short recordings (<0.5s) — usually noise/accidental triggers
    if duration_s < 0.5:
        logger.info(f"ASR: skipping short audio ({duration_s:.1f}s < 0.5s)")
        return ""

    wav_file = io.BytesIO(wav_bytes)
    wav_file.name = "audio.wav"

    client = _get_client(session)
    asr_model = (session.config.get("openai_asr_model", settings.openai_asr_model)
                 if session else settings.openai_asr_model)

    transcript = await client.audio.transcriptions.create(
        model=asr_model,
        file=wav_file,
        temperature=0,
        # Optimized prompt for fast speech and mixed languages
        # 提示词优化：明确快速说话场景，提高识别准确度
        prompt="HiTony语音助手对话。用户可能快速说话，使用中文、英文或混合语言。Natural conversation, fast speech, Chinese/English mixed.",
    )

    text = transcript.text.strip()
    logger.info(f"ASR result: {text}")

    # Filter known Whisper hallucinations (noise/silence → fake output)
    normalized = text.lower().rstrip(".!?,")
    if normalized in _HALLUCINATIONS:
        logger.warning(f"ASR: filtered hallucination: '{text}'")
        return ""

    return text
