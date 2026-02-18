"""Player control tools — pause, resume, stop for music playback."""
from ..registry import register_tool, ToolResult
import random


@register_tool("player.pause", description="Pause currently playing music", category="player")
async def player_pause(session=None, **kwargs) -> ToolResult:
    if session and session.music_playing:
        session._music_pause_event.clear()
        session.music_paused = True
        return ToolResult(type="tts", text="已暂停")
    return ToolResult(type="tts", text="没有正在播放的音乐")


@register_tool("player.resume", description="Resume paused music", category="player")
async def player_resume(session=None, **kwargs) -> ToolResult:
    if session and session.music_paused:
        session._music_pause_event.set()
        session.music_paused = False
        return ToolResult(type="tts", text="继续播放")
    return ToolResult(type="tts", text="没有暂停的音乐")


@register_tool("player.stop", description="Stop music playback", category="player")
async def player_stop(session=None, **kwargs) -> ToolResult:
    if session and session.music_playing:
        session.music_abort = True
        session._music_pause_event.set()
        return ToolResult(type="tts", text="已停止播放")
    return ToolResult(type="tts", text="没有正在播放的音乐")


@register_tool("player.next", description="Skip to next song (plays random popular music)", category="player")
async def player_next(session=None, **kwargs) -> ToolResult:
    """Skip current song and play next (random popular song)."""
    import asyncio
    from ..music import search_and_stream

    # Stop current music if playing
    if session and session.music_playing:
        session.music_abort = True
        session._music_pause_event.set()
        # Wait a bit for current stream to stop
        await asyncio.sleep(0.3)

    # Play a random popular song
    queries = ["热门歌曲", "流行音乐", "经典老歌", "抖音热歌", "网红歌曲"]
    query = random.choice(queries)

    youtube_api_key = ""
    if session and hasattr(session, "config"):
        youtube_api_key = session.config.youtube_api_key or ""

    try:
        title, generator = await search_and_stream(query, youtube_api_key=youtube_api_key)
        return ToolResult(
            type="music",
            text=f"为你播放：{title}",
            data={
                "title": title,
                "generator": generator,
            }
        )
    except Exception as e:
        return ToolResult(type="tts", text=f"切歌失败：{str(e)}")
