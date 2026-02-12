"""Edge TTS synthesis — free Microsoft TTS via edge-tts package.
No API key required. Supports many voices and languages.
"""
import asyncio
import io
import logging
import struct
from typing import Optional

import opuslib

from .config import settings

logger = logging.getLogger(__name__)

# Default Edge TTS voices (good quality, low latency)
EDGE_VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",     # Chinese female (warm)
    "yunxi": "zh-CN-YunxiNeural",            # Chinese male
    "xiaoyi": "zh-CN-XiaoyiNeural",          # Chinese female (lively)
    "jenny": "en-US-JennyNeural",            # English female
    "guy": "en-US-GuyNeural",                # English male
    "aria": "en-US-AriaNeural",              # English female (natural)
}

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def _resolve_voice(voice_name: str) -> str:
    """Resolve short name to full Edge TTS voice name."""
    if not voice_name:
        return DEFAULT_VOICE
    # If it's already a full name (contains '-'), use as-is
    if "-" in voice_name:
        return voice_name
    # Try short name lookup
    return EDGE_VOICES.get(voice_name.lower(), DEFAULT_VOICE)


async def synthesize_edge_tts(text: str, voice: str = "") -> list:
    """Synthesize TTS using Edge TTS and return list of Opus packets.

    Args:
        text: Text to synthesize
        voice: Voice name (short name like 'xiaoxiao' or full like 'zh-CN-XiaoxiaoNeural')

    Returns:
        List of Opus-encoded packets (60ms frames, 16kHz mono)
    """
    try:
        import edge_tts
    except ImportError:
        logger.error("edge-tts package not installed. Run: pip install edge-tts")
        raise RuntimeError("edge-tts not installed")

    resolved_voice = _resolve_voice(voice)
    logger.info(f"Edge TTS: synthesizing '{text[:50]}...' with voice={resolved_voice}")

    # Edge TTS returns MP3 by default; we need to convert to PCM
    communicate = edge_tts.Communicate(text, resolved_voice)

    # Collect all audio data
    audio_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])

    if not audio_data:
        logger.warning("Edge TTS returned empty audio")
        return []

    logger.info(f"Edge TTS: received {len(audio_data)} bytes MP3")

    # Convert MP3 to PCM16 16kHz mono using ffmpeg (in thread pool)
    loop = asyncio.get_event_loop()
    opus_packets = await loop.run_in_executor(None, _mp3_to_opus, bytes(audio_data))

    logger.info(f"Edge TTS: encoded {len(opus_packets)} Opus packets")
    return opus_packets


def _mp3_to_opus(mp3_data: bytes) -> list:
    """Convert MP3 bytes to Opus packets via ffmpeg (CPU-bound, runs in thread pool)."""
    import subprocess

    # ffmpeg: MP3 → PCM16 16kHz mono
    proc = subprocess.run(
        ["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1"],
        input=mp3_data,
        capture_output=True,
        timeout=30,
    )

    if proc.returncode != 0:
        logger.error(f"ffmpeg failed: {proc.stderr[:200]}")
        raise RuntimeError("ffmpeg conversion failed")

    pcm_16k = proc.stdout
    logger.info(f"Edge TTS: converted to {len(pcm_16k)} bytes PCM (16kHz)")

    # Opus encode (same as tts.py)
    encoder = opuslib.Encoder(settings.pcm_sample_rate, settings.pcm_channels, opuslib.APPLICATION_VOIP)
    encoder.bitrate = 24000

    opus_packets = []
    frame_size = 1920  # 960 samples * 2 bytes

    for i in range(0, len(pcm_16k), frame_size):
        frame = pcm_16k[i:i + frame_size]
        if len(frame) < frame_size:
            frame = frame + b'\x00' * (frame_size - len(frame))
        opus_packet = encoder.encode(frame, 960)
        opus_packets.append(opus_packet)

    return opus_packets
