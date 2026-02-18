"""Volume control tool."""
import json
from ..registry import register_tool, ToolResult, ToolParam


async def _send_volume(session, level: int) -> bool:
    """Send volume command to device via WS. Returns True on success."""
    from ..ws_server import get_active_connection

    conn = get_active_connection(session.device_id) if hasattr(session, 'device_id') else None
    if not conn:
        return False

    ws, _ = conn
    msg = json.dumps({"type": "volume", "level": level})
    await ws.send(msg)
    return True


@register_tool(
    name="volume.set",
    description="Set device volume (0-100)",
    params=[
        ToolParam(name="level", type="integer", description="Volume level (0=mute, 100=max)", required=True)
    ]
)
async def volume_set(level: int, session=None, **kwargs) -> ToolResult:
    """Set device volume to specified level."""
    if not session:
        return ToolResult(type="error", text="No active session")

    level = max(0, min(100, level))
    session.volume = level

    if not await _send_volume(session, level):
        return ToolResult(type="error", text="Device not connected")

    if level == 0:
        return ToolResult(type="tts", text="已静音")
    elif level <= 30:
        return ToolResult(type="tts", text=f"音量设为{level}%，较小")
    elif level <= 70:
        return ToolResult(type="tts", text=f"音量设为{level}%")
    else:
        return ToolResult(type="tts", text=f"音量设为{level}%，较大")


@register_tool(
    name="volume.up",
    description="Increase volume by 10%",
    params=[]
)
async def volume_up(session=None, **kwargs) -> ToolResult:
    """Increase volume by 10%."""
    if not session:
        return ToolResult(type="error", text="No active session")

    current = getattr(session, 'volume', 60)
    new_level = min(100, current + 10)
    session.volume = new_level

    if not await _send_volume(session, new_level):
        return ToolResult(type="tts", text=f"音量调到{new_level}%")

    return ToolResult(type="tts", text=f"音量调到{new_level}%")


@register_tool(
    name="volume.down",
    description="Decrease volume by 10%",
    params=[]
)
async def volume_down(session=None, **kwargs) -> ToolResult:
    """Decrease volume by 10%."""
    if not session:
        return ToolResult(type="error", text="No active session")

    current = getattr(session, 'volume', 60)
    new_level = max(0, current - 10)
    session.volume = new_level

    if not await _send_volume(session, new_level):
        return ToolResult(type="tts", text=f"音量调到{new_level}%")

    return ToolResult(type="tts", text=f"音量调到{new_level}%")
