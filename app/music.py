"""Music streaming: yt-dlp fetch -> ffmpeg -> Opus encode -> async generator."""
import asyncio
import json
import logging
from typing import AsyncGenerator, Tuple

import opuslib

from .config import settings

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 960       # 60ms @ 16kHz
FRAME_BYTES = FRAME_SAMPLES * 2  # 960 samples * 2 bytes (int16)
READ_CHUNK = FRAME_BYTES * 4     # Read ~240ms at a time


async def search_and_stream(query: str) -> Tuple[str, AsyncGenerator[bytes, None]]:
    """Search YouTube and stream audio as Opus packets.

    Returns (title, async_generator_of_opus_packets).
    The generator yields one Opus packet (~60ms) at a time.
    """
    # Step 1: Get metadata (title) via yt-dlp --dump-json
    search_query = query if query.startswith("http") else f"ytsearch:{query}"

    logger.info(f"Music: fetching metadata for '{search_query}'")
    meta_proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--dump-json", "--no-download", search_query,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    meta_stdout, meta_stderr = await meta_proc.communicate()

    if meta_proc.returncode != 0:
        err = meta_stderr.decode(errors="replace")[:200]
        raise RuntimeError(f"yt-dlp metadata failed: {err}")

    info = json.loads(meta_stdout)
    title = info.get("title", "Unknown")
    url = info.get("webpage_url", search_query)
    duration = info.get("duration", 0)
    logger.info(f"Music: '{title}' ({duration}s) url={url}")

    # Enforce max duration
    if duration > settings.music_max_duration_s:
        raise RuntimeError(f"Track too long ({duration}s > {settings.music_max_duration_s}s max)")

    # Step 2: yt-dlp → ffmpeg pipe (no shell — avoids injection)
    ytdlp_proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "-f", "bestaudio", "--no-warnings", "-o", "-", url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    ffmpeg_proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1",
        stdin=ytdlp_proc.stdout,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Allow ytdlp_proc.stdout to be consumed by ffmpeg
    ytdlp_proc.stdout = None

    async def opus_generator() -> AsyncGenerator[bytes, None]:
        encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_AUDIO)
        encoder.bitrate = 24000
        buffer = b""

        try:
            while True:
                chunk = await ffmpeg_proc.stdout.read(READ_CHUNK)
                if not chunk:
                    break
                buffer += chunk
                while len(buffer) >= FRAME_BYTES:
                    frame = buffer[:FRAME_BYTES]
                    buffer = buffer[FRAME_BYTES:]
                    yield encoder.encode(frame, FRAME_SAMPLES)

            # Encode remaining (pad with silence)
            if buffer:
                frame = buffer + b'\x00' * (FRAME_BYTES - len(buffer))
                yield encoder.encode(frame, FRAME_SAMPLES)
        finally:
            for proc in (ffmpeg_proc, ytdlp_proc):
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except (ProcessLookupError, asyncio.TimeoutError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            logger.info(f"Music: audio processes terminated for '{title}'")

    return title, opus_generator()
