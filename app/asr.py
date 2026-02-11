import io
import struct
import logging
from openai import AsyncOpenAI
from .config import settings

logger = logging.getLogger(__name__)

# API key and base_url are already sanitized in config.py
_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)

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


async def transcribe_pcm(pcm_bytes: bytes) -> str:
    """Transcribe PCM audio using OpenAI Whisper API"""
    wav_bytes = _pcm_to_wav(pcm_bytes)
    logger.info(f"ASR: sending {len(pcm_bytes)} bytes PCM ({len(wav_bytes)} bytes WAV) to Whisper")

    wav_file = io.BytesIO(wav_bytes)
    wav_file.name = "audio.wav"

    transcript = await _client.audio.transcriptions.create(
        model=settings.openai_asr_model,
        file=wav_file,
        # No language forced — Whisper auto-detects (Chinese + English + others)
    )

    text = transcript.text.strip()
    logger.info(f"ASR result: {text}")

    # Filter known Whisper hallucinations (noise/silence → fake output)
    normalized = text.lower().rstrip(".!?,")
    if normalized in _HALLUCINATIONS:
        logger.warning(f"ASR: filtered hallucination: '{text}'")
        return ""

    return text
