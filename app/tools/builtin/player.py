"""Player control tools — pause, resume, stop for music playback."""
from ..registry import register_tool, ToolResult


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
