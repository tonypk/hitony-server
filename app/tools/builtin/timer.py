"""Timer tool — in-memory countdown with server-push TTS notification."""
import asyncio
import logging
from typing import Dict

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)

# Active timers: session_id → list of asyncio.Task
_active_timers: Dict[str, list] = {}


@register_tool(
    "timer.set",
    description="Set a countdown timer (e.g. 5 minutes). Notifies via TTS when done.",
    params=[
        ToolParam("seconds", description="countdown duration in seconds"),
        ToolParam("label", description="timer label (e.g. '5分钟倒计时')", required=False),
    ],
    category="timer",
)
async def timer_set(seconds: str, label: str = "", session=None, **kwargs) -> ToolResult:
    try:
        secs = int(seconds)
    except (ValueError, TypeError):
        return ToolResult(type="tts", text="抱歉，我没有理解那个时间。请说比如倒计时5分钟。")

    if secs <= 0:
        return ToolResult(type="tts", text="时间必须大于0。")
    if secs > 7200:
        return ToolResult(type="tts", text="最多支持2小时倒计时。")

    if not session:
        return ToolResult(type="error", text="无法设置倒计时。")

    # Get WS connection for server-push when timer fires
    from ...ws_server import get_active_connection

    sid = session.session_id
    device_id = session.device_id
    timer_label = label or f"{secs}秒倒计时"

    # Schedule the timer as a background task
    task = asyncio.create_task(_timer_fire(secs, timer_label, device_id, sid))

    if sid not in _active_timers:
        _active_timers[sid] = []
    _active_timers[sid].append(task)

    # Clean up finished tasks
    _active_timers[sid] = [t for t in _active_timers[sid] if not t.done()]

    if secs >= 60:
        mins = secs // 60
        remaining = secs % 60
        time_str = f"{mins}分钟" + (f"{remaining}秒" if remaining else "")
    else:
        time_str = f"{secs}秒"

    return ToolResult(type="tts", text=f"好的，{time_str}倒计时已开始。")


@register_tool(
    "timer.cancel",
    description="Cancel all active timers for the current session",
    params=[],
    category="timer",
)
async def timer_cancel(session=None, **kwargs) -> ToolResult:
    if not session:
        return ToolResult(type="error", text="无法取消倒计时。")

    sid = session.session_id
    tasks = _active_timers.get(sid, [])
    # Filter to only running tasks
    active = [t for t in tasks if not t.done()]

    if not active:
        return ToolResult(type="tts", text="当前没有正在运行的倒计时。")

    for t in active:
        t.cancel()

    count = len(active)
    _active_timers[sid] = []
    return ToolResult(type="tts", text=f"已取消{count}个倒计时。")


async def _timer_fire(seconds: int, label: str, device_id: str, session_id: str):
    """Wait for the timer duration, then push a TTS notification to the device."""
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        logger.info(f"[{session_id}] Timer cancelled: {label}")
        return

    logger.info(f"[{session_id}] Timer fired: {label}")

    # Push TTS notification to device
    from ...ws_server import get_active_connection
    conn = get_active_connection(device_id)
    if not conn:
        logger.warning(f"[{session_id}] Timer fired but device {device_id} offline")
        return

    ws, session = conn

    try:
        import json
        from ...tts import synthesize_tts
        from ...pipeline import ws_send_safe, _send_tts_round

        notification = f"时间到！{label}已结束。"
        opus_packets = await synthesize_tts(notification, session=session)
        await _send_tts_round(ws, session, opus_packets, notification)
        logger.info(f"[{session_id}] Timer notification sent: {label}")
    except Exception as e:
        logger.error(f"[{session_id}] Timer notification failed: {e}")
