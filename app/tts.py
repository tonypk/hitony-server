import asyncio
import logging
import struct
from typing import Optional
import opuslib
from openai import AsyncOpenAI
from .config import settings
from .session import Session

logger = logging.getLogger(__name__)

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


def _resample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Resample PCM16 from 24kHz to 16kHz using simple linear interpolation.
    OpenAI TTS pcm format outputs 24kHz 16-bit mono."""
    samples_24k = struct.unpack(f'<{len(pcm_24k) // 2}h', pcm_24k)
    n_in = len(samples_24k)
    n_out = n_in * 2 // 3  # 16000/24000 = 2/3

    samples_16k = []
    for i in range(n_out):
        # Map output index to input position
        pos = i * 3.0 / 2.0
        idx = int(pos)
        frac = pos - idx
        if idx + 1 < n_in:
            val = samples_24k[idx] * (1.0 - frac) + samples_24k[idx + 1] * frac
        else:
            val = samples_24k[idx] if idx < n_in else 0
        # Clamp to int16 range
        val = max(-32768, min(32767, int(val)))
        samples_16k.append(val)

    return struct.pack(f'<{len(samples_16k)}h', *samples_16k)


async def synthesize_tts(text: str, session: Optional[Session] = None) -> list:
    """Synthesize TTS using configured provider and return list of Opus packets.
    Supports 'openai' (default) and 'edge' (free, no API key needed).
    """
    # Check TTS provider
    tts_provider = ""
    if session:
        tts_provider = session.config.get("tts_provider", "")
    if tts_provider == "edge":
        from .edge_tts_synth import synthesize_edge_tts
        tts_voice = (session.config.get("openai_tts_voice", "xiaoxiao")
                     if session else "xiaoxiao")
        return await synthesize_edge_tts(text, voice=tts_voice)

    # Default: OpenAI TTS
    client = _get_client(session)
    tts_model = (session.config.get("openai_tts_model", settings.openai_tts_model)
                 if session else settings.openai_tts_model)
    tts_voice = (session.config.get("openai_tts_voice", settings.openai_tts_voice)
                 if session else settings.openai_tts_voice)

    logger.info(f"TTS: synthesizing '{text[:50]}...' with {tts_model}/{tts_voice}")

    response = await client.audio.speech.create(
        model=tts_model,
        voice=tts_voice,
        input=text,
        response_format="pcm",  # Raw PCM16 24kHz mono
    )

    pcm_24k = response.content
    logger.info(f"TTS: received {len(pcm_24k)} bytes PCM (24kHz)")

    # Run CPU-bound resample + Opus encode in thread pool to avoid blocking event loop.
    loop = asyncio.get_event_loop()
    opus_packets = await loop.run_in_executor(None, _resample_and_encode, pcm_24k)

    logger.info(f"TTS: encoded {len(opus_packets)} Opus packets, sizes: {[len(p) for p in opus_packets[:5]]}")
    return opus_packets


def _resample_and_encode(pcm_24k: bytes) -> list:
    """CPU-bound: resample 24k→16k + Opus encode. Runs in thread pool."""
    pcm_16k = _resample_24k_to_16k(pcm_24k)
    logger.info(f"TTS: resampled to {len(pcm_16k)} bytes PCM (16kHz)")

    encoder = opuslib.Encoder(settings.pcm_sample_rate, settings.pcm_channels, opuslib.APPLICATION_VOIP)
    encoder.bitrate = 24000

    opus_packets = []
    frame_size = 1920  # 960 samples * 2 bytes per sample

    for i in range(0, len(pcm_16k), frame_size):
        frame = pcm_16k[i:i + frame_size]
        if len(frame) < frame_size:
            frame = frame + b'\x00' * (frame_size - len(frame))
        opus_packet = encoder.encode(frame, 960)
        opus_packets.append(opus_packet)

    return opus_packets
