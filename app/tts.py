import httpx
import opuslib
from .config import settings

async def synthesize_tts(text: str) -> bytes:
    """Synthesize TTS and return Opus-encoded audio"""
    async with httpx.AsyncClient(timeout=120) as client:
        payload = {
            "text": text,
            "rate": settings.pcm_sample_rate,
            "channels": settings.pcm_channels,
            "format": "pcm16"
        }
        resp = await client.post(settings.tts_url, json=payload)
        resp.raise_for_status()
        pcm_data = resp.content

    # Encode PCM16 to Opus (60ms frames @ 16kHz = 960 samples = 1920 bytes)
    encoder = opuslib.Encoder(settings.pcm_sample_rate, settings.pcm_channels, opuslib.APPLICATION_AUDIO)

    # Set bitrate to auto for best quality
    encoder.bitrate = opuslib.OPUS_AUTO

    opus_packets = []
    frame_size = 1920  # 60ms @ 16kHz mono PCM16 = 960 samples * 2 bytes

    for i in range(0, len(pcm_data), frame_size):
        frame = pcm_data[i:i+frame_size]

        # Pad last frame if needed
        if len(frame) < frame_size:
            frame = frame + b'\x00' * (frame_size - len(frame))

        # Encode to Opus
        opus_packet = encoder.encode(frame, 960)  # 960 samples per 60ms frame
        opus_packets.append(opus_packet)

    # Return list of Opus packets (not concatenated - send individually)
    return opus_packets
