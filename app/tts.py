import asyncio
import logging
import struct
from typing import Optional
from collections import OrderedDict
import numpy as np
import opuslib
from openai import AsyncOpenAI
from .config import settings
from .session import Session

logger = logging.getLogger(__name__)

# LRU cache for short TTS phrases (max 50 entries)
_tts_cache: OrderedDict[tuple, list] = OrderedDict()
_TTS_CACHE_MAX = 50
_TTS_CACHE_MAX_CHARS = 20  # Only cache phrases <= 20 chars

_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)

# Per-user client cache with LRU eviction: (base_url, api_key) → AsyncOpenAI
_CLIENT_CACHE_MAX = 20
_client_cache: OrderedDict[tuple[str, str], AsyncOpenAI] = OrderedDict()


def _get_client(session: Optional[Session] = None) -> AsyncOpenAI:
    """Return cached per-user client if session has custom API key, else global."""
    if session and session.config.openai_api_key:
        base_url = session.config.get("openai_base_url", settings.openai_base_url)
        key = (base_url, session.config.openai_api_key)
        if key not in _client_cache:
            if len(_client_cache) >= _CLIENT_CACHE_MAX:
                _client_cache.popitem(last=False)
            _client_cache[key] = AsyncOpenAI(api_key=session.config.openai_api_key, base_url=base_url)
        _client_cache.move_to_end(key)
        return _client_cache[key]
    return _client


def _resample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Resample PCM16 from 24kHz to 16kHz using numpy (fast vectorized interpolation).
    OpenAI TTS pcm format outputs 24kHz 16-bit mono."""
    samples_24k = np.frombuffer(pcm_24k, dtype=np.int16).astype(np.float32)
    n_in = len(samples_24k)
    n_out = n_in * 2 // 3  # 16000/24000 = 2/3

    x_in = np.arange(n_in)
    x_out = np.linspace(0, n_in - 1, n_out)
    samples_16k = np.interp(x_out, x_in, samples_24k)
    samples_16k = np.clip(samples_16k, -32768, 32767).astype(np.int16)

    return samples_16k.tobytes()


async def synthesize_tts(text: str, session: Optional[Session] = None) -> list:
    """Synthesize TTS using configured provider and return list of Opus packets.
    Supports 'openai' (default) and 'edge' (free, no API key needed).
    Short phrases (<=20 chars) are cached to avoid redundant API calls.
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

    # Check LRU cache for short phrases
    if len(text) <= _TTS_CACHE_MAX_CHARS:
        cache_key = (text, tts_model, tts_voice)
        if cache_key in _tts_cache:
            _tts_cache.move_to_end(cache_key)
            logger.info(f"TTS cache hit: '{text}' ({len(_tts_cache[cache_key])} packets)")
            return _tts_cache[cache_key]

    logger.info(f"TTS: synthesizing '{text[:50]}...' with {tts_model}/{tts_voice}")

    # Try user's OpenClaw first (Pro mode), fallback to default if unsupported
    try:
        response = await client.audio.speech.create(
            model=tts_model,
            voice=tts_voice,
            input=text,
            response_format="pcm",  # Raw PCM16 24kHz mono
        )
        pcm_24k = response.content
        logger.info(f"TTS: received {len(pcm_24k)} bytes PCM (24kHz)")
    except Exception as e:
        # Fallback to default API if user's OpenClaw doesn't support TTS
        if session and session.config.openai_base_url:
            logger.warning(f"TTS: Pro mode failed ({e}), falling back to default API")
            response = await _client.audio.speech.create(
                model=settings.openai_tts_model,
                voice=settings.openai_tts_voice,
                input=text,
                response_format="pcm",
            )
            pcm_24k = response.content
            logger.info(f"TTS: fallback received {len(pcm_24k)} bytes PCM (24kHz)")
        else:
            raise  # Re-raise if not in Pro mode

    # Run CPU-bound resample + Opus encode in thread pool to avoid blocking event loop.
    loop = asyncio.get_event_loop()
    opus_packets = await loop.run_in_executor(None, _resample_and_encode, pcm_24k)

    logger.info(f"TTS: encoded {len(opus_packets)} Opus packets, sizes: {[len(p) for p in opus_packets[:5]]}")

    # Cache short phrases for future reuse
    if len(text) <= _TTS_CACHE_MAX_CHARS:
        cache_key = (text, tts_model, tts_voice)
        _tts_cache[cache_key] = opus_packets
        if len(_tts_cache) > _TTS_CACHE_MAX:
            _tts_cache.popitem(last=False)  # Remove oldest entry
        logger.info(f"TTS cached: '{text}' (cache size: {len(_tts_cache)})")

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
