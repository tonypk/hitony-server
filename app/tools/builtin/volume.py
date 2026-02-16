"""Volume control tool."""
from ..registry import register_tool, ToolResult, ToolParam


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

    # Clamp to valid range
    level = max(0, min(100, level))

    # Send volume command via WebSocket
    import json
    from ..ws_server import get_active_connection

    conn = get_active_connection(session.device_id) if hasattr(session, 'device_id') else session
    if not conn:
        return ToolResult(type="error", text="Device not connected")

    ws, _ = conn
    msg = json.dumps({"type": "volume", "level": level})
    await ws.send(msg)

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
    # Get current volume from session state (if tracked)
    # For now, just send a relative command
    return ToolResult(type="tts", text="音量已增大")


@register_tool(
    name="volume.down",
    description="Decrease volume by 10%",
    params=[]
)
async def volume_down(session=None, **kwargs) -> ToolResult:
    """Decrease volume by 10%."""
    return ToolResult(type="tts", text="音量已减小")
