"""Music streaming: YouTube API / yt-dlp search -> ffmpeg -> Opus encode -> async generator."""
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional, Tuple

import httpx
import opuslib

from .config import settings

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 960       # 60ms @ 16kHz
FRAME_BYTES = FRAME_SAMPLES * 2  # 960 samples * 2 bytes (int16)
READ_CHUNK = FRAME_BYTES * 8     # Read ~480ms at a time (smoother pipe reads)

YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/search"


async def _youtube_api_search(query: str, api_key: str) -> Tuple[str, str, Optional[int]]:
    """Search via YouTube Data API v3. Returns (title, video_url, duration_or_None)."""
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoCategoryId": "10",  # Music category
        "videoDuration": "medium",  # 4-20 min, filters out compilations
        "maxResults": 1,
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(YOUTUBE_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("items", [])
    if not items:
        raise RuntimeError(f"YouTube API: no results for '{query}'")

    item = items[0]
    video_id = item["id"]["videoId"]
    title = item["snippet"]["title"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info(f"Music: YouTube API found '{title}' -> {url}")
    return title, url, None  # duration not available from search.list


async def _ytdlp_search(query: str) -> Tuple[str, str, int]:
    """Search via yt-dlp ytsearch. Returns (title, video_url, duration).

    Searches top 5 results and picks the first one under max duration.
    """
    if query.startswith("http"):
        search_query = query
        max_results = 1
    else:
        max_results = 5
        search_query = f"ytsearch{max_results}:{query}"

    logger.info(f"Music: yt-dlp search for '{search_query}'")

    meta_proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--dump-json", "--no-download", "--flat-playlist", search_query,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    meta_stdout, meta_stderr = await meta_proc.communicate()

    if meta_proc.returncode != 0:
        err = meta_stderr.decode(errors="replace")[:200]
        raise RuntimeError(f"yt-dlp metadata failed: {err}")

    # Parse results (one JSON object per line for multi-result search)
    candidates = []
    for line in meta_stdout.decode(errors="replace").strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
            candidates.append(info)
        except json.JSONDecodeError:
            continue

    if not candidates:
        raise RuntimeError(f"yt-dlp: no results for '{query}'")

    # Pick first result under max duration
    max_dur = settings.music_max_duration_s
    for info in candidates:
        duration = info.get("duration", 0) or 0
        if duration <= max_dur:
            title = info.get("title", "Unknown")
            url = info.get("webpage_url") or info.get("url", search_query)
            logger.info(f"Music: yt-dlp picked '{title}' ({duration}s) from {len(candidates)} results")
            return title, url, duration

    # All results too long — return first one anyway (will be caught by caller)
    info = candidates[0]
    title = info.get("title", "Unknown")
    url = info.get("webpage_url") or info.get("url", search_query)
    duration = info.get("duration", 0) or 0
    logger.warning(f"Music: all {len(candidates)} results exceed {max_dur}s, using first: '{title}' ({duration}s)")
    return title, url, duration


async def search_and_stream(query: str, youtube_api_key: str = "") -> Tuple[str, AsyncGenerator[bytes, None]]:
    """Search YouTube and stream audio as Opus packets.

    Returns (title, async_generator_of_opus_packets).
    The generator yields one Opus packet (~60ms) at a time.

    If youtube_api_key is provided, uses official YouTube Data API v3 for search.
    Otherwise falls back to yt-dlp ytsearch.
    """
    is_url = query.startswith("http")

    if is_url:
        title, url, duration = await _ytdlp_search(query)
    elif youtube_api_key:
        try:
            title, url, duration = await _youtube_api_search(query, youtube_api_key)
        except Exception as e:
            logger.warning(f"Music: YouTube API search failed ({e}), falling back to yt-dlp")
            title, url, duration = await _ytdlp_search(query)
    else:
        title, url, duration = await _ytdlp_search(query)

    logger.info(f"Music: '{title}' (duration={duration}) url={url}")

    # Enforce max duration (skip if duration unknown from API search)
    if duration and duration > settings.music_max_duration_s:
        raise RuntimeError(f"Track too long ({duration}s > {settings.music_max_duration_s}s max)")

    # Stream audio: yt-dlp → ffmpeg pipe → Opus encode
    ytdlp_proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "-f", "bestaudio", "--no-warnings", "-o", "-", url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    ffmpeg_proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Manual pipe: ytdlp → ffmpeg (background task)
    async def pipe_ytdlp_to_ffmpeg():
        try:
            while True:
                chunk = await ytdlp_proc.stdout.read(65536)
                if not chunk:
                    break
                ffmpeg_proc.stdin.write(chunk)
                await ffmpeg_proc.stdin.drain()
        finally:
            ffmpeg_proc.stdin.close()

    pipe_task = asyncio.create_task(pipe_ytdlp_to_ffmpeg())

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
            # Cancel manual piping task
            pipe_task.cancel()
            try:
                await pipe_task
            except asyncio.CancelledError:
                pass

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
